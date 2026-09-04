"""Running the tool calls of one round: batching, threads, order.

The dispatcher knows nothing about messages, persistence or streams. It
takes the calls a reply asked for and returns what came of each, in the
order the model asked for them whatever order they landed in.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from django.db import connections

from workspace.ai.services.call_order import set_call_position
from workspace.common.logging import scrub

from .model import ToolCall

logger = logging.getLogger(__name__)


@dataclass
class CallOutcome:
    """One tool call of a round, and what came of it.

    Filled in three steps - planned on the main thread in call order, run
    (possibly off it), then read back in call order. ``result`` holds the
    tool's answer, or the refusal a policy wrote in its place.
    """

    call: ToolCall
    position: int
    result: object = None
    refusal: str | None = None
    error: BaseException | None = None

    @property
    def refused(self) -> bool:
        return self.refusal is not None


@dataclass
class RoundOutcome:
    """Everything one round did, in call order.

    A cancellation read mid-round ends the planning right there: the calls
    already planned still run, the ones after them are never dispatched
    and appear nowhere.
    """

    outcomes: list[CallOutcome]
    cancelled: bool = False

    @property
    def executed(self) -> int:
        """Calls that actually ran, as opposed to being refused."""
        return sum(1 for outcome in self.outcomes if not outcome.refused)


def _batch_calls(tool_calls, concurrent_names, limit):
    """Split a round's calls into batches that may run in one dispatch.

    Consecutive independent calls share a batch, up to *limit*; every other
    call is a batch of its own, which keeps a write in the order the model
    asked for it and keeps every read before it strictly ahead of it.
    """
    batch = []
    for tool_call in tool_calls:
        if limit > 1 and tool_call.name in concurrent_names:
            batch.append(tool_call)
            if len(batch) == limit:
                yield batch
                batch = []
            continue
        if batch:
            yield batch
            batch = []
        yield [tool_call]
    if batch:
        yield batch


class Dispatcher:
    """Runs the calls of a round against *toolset* for one response.

    *policies* are consulted before each call, in order; *observers* hear
    of each call starting, returning and being read back. *is_cancelled*
    is read before every dispatch, so a cancellation never starts a call
    that writes memories, schedules messages or bills an image.

    The call position climbs across rounds, never restarting: a tool that
    leaves media for the caller to attach stamps it with the rank the
    model asked for it in, and a later round's media go after an earlier
    one's.
    """

    def __init__(
        self,
        toolset,
        *,
        concurrency,
        user,
        bot,
        conversation_id=None,
        context=None,
        is_cancelled=None,
        policies=(),
        observers=(),
    ):
        self._toolset = toolset
        self._concurrency = concurrency
        self._concurrent_names = toolset.concurrent_names()
        self._user = user
        self._bot = bot
        self._conversation_id = conversation_id
        self._context = context if context is not None else {}
        self._is_cancelled = is_cancelled
        self._policies = list(policies)
        self._observers = list(observers)
        self._position = 0

    def run_round(self, calls: list[ToolCall]) -> RoundOutcome:
        outcomes = []
        cancelled = False
        for batch in _batch_calls(calls, self._concurrent_names, self._concurrency):
            planned = []
            for call in batch:
                # Read before planning, not after: past this point the tool
                # writes memories, schedules messages or bills an image, and
                # none of that should happen once the user has cancelled.
                if self._is_cancelled and self._is_cancelled():
                    cancelled = True
                    break
                self._position += 1
                outcome = CallOutcome(call=call, position=self._position)
                planned.append(outcome)
                refusal = self._refusal(call)
                if refusal is not None:
                    # The refusal is what the model reads in place of a result.
                    outcome.refusal = outcome.result = refusal
                    continue
                for observer in self._observers:
                    observer.on_call_start(call)
            self._run_batch(planned)
            for outcome in planned:
                for observer in self._observers:
                    observer.on_call_end(outcome)
                if outcome.error is not None:
                    raise outcome.error
            outcomes.extend(planned)
            if cancelled:
                break
        return RoundOutcome(outcomes, cancelled=cancelled)

    def _refusal(self, call):
        for policy in self._policies:
            refusal = policy.refusal(call)
            if refusal is not None:
                logger.info("Refused tool call %s", scrub(call.name))
                return refusal
        return None

    def _execute(self, outcome):
        """Run one call and report its return from wherever it ran.

        The report goes out as soon as the handler returns, so the row of a
        quick call stops spinning without waiting for the slow one it was
        dispatched with.
        """
        # Read by a tool that leaves an image for the caller to attach:
        # appended in completion order, they would reach the reply in a
        # different order than the model asked for them in.
        set_call_position(outcome.position)
        try:
            return self._toolset.execute(
                outcome.call,
                user=self._user,
                bot=self._bot,
                conversation_id=self._conversation_id,
                context=self._context,
            )
        finally:
            for observer in self._observers:
                observer.on_call_return(outcome.call)

    def _execute_off_thread(self, outcome):
        """Run one call in a pool thread and hand its connections back.

        Django opens a connection per thread on first query and nothing
        closes the ones a pool thread leaves behind, so a worker process
        would collect one for every parallel tool call it has ever run.
        """
        try:
            return self._execute(outcome)
        finally:
            connections.close_all()

    def _run_batch(self, planned):
        """Execute the calls of one batch, storing each outcome on its entry.

        A single call runs inline: a thread buys nothing and costs a
        database connection. Exceptions are captured rather than raised so
        the caller can still read them back in call order.
        """
        pending = [outcome for outcome in planned if not outcome.refused]
        if len(pending) <= 1:
            for outcome in pending:
                try:
                    outcome.result = self._execute(outcome)
                except Exception as exc:
                    outcome.error = exc
            return
        # One worker per call: everything dispatched starts immediately, so
        # a cancellation checked before dispatch cannot be outrun by a
        # queued call.
        with ThreadPoolExecutor(max_workers=len(pending)) as pool:
            futures = [pool.submit(self._execute_off_thread, o) for o in pending]
        for outcome, future in zip(pending, futures, strict=True):
            try:
                outcome.result = future.result()
            except Exception as exc:
                outcome.error = exc
