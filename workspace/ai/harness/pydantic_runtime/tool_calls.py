"""The tool calls of a run: what the model may call, and how each call runs.

Pydantic AI decides when a call runs - the calls of a reply start
together, except around a tool that must run alone. What a call does when
its turn comes is decided here: on which thread, in whose language, after
which refusals, and who hears of it.

Two threads matter. The run's event loop lives in a thread of its own, and
the thread that started the run stays free to do the run's synchronous
work. A tool that runs alone is sent back to that thread, where it finds
the database connection, and the open transaction, of whoever started the
run. Tools that run together each take a pool thread and hand their
connections back when they return.
"""

import asyncio
import base64
import binascii
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass
from itertools import groupby

from asgiref.sync import sync_to_async
from django.db import connections
from django.utils import translation
from pydantic_ai import RunContext, Tool
from pydantic_ai.messages import BinaryContent, ModelRequest, ToolReturnPart
from pydantic_ai.toolsets import FunctionToolset

from workspace.ai.harness.dispatch import CallOutcome
from workspace.ai.harness.model import ToolCall
from workspace.ai.harness.observers import notify
from workspace.ai.services.call_order import set_call_position
from workspace.ai.tool_registry import tool_failure
from workspace.common.logging import scrub
from workspace.users.services.settings import get_user_language

logger = logging.getLogger(__name__)

# Stands in for the result of a call a cancellation kept from running.
_CANCELLED = "not executed: the reply was cancelled"

_NO_PARAMETERS = {"type": "object", "properties": {}}


def on_task_thread(function, *args):
    """Run *function* on the thread that started the run, and wait for it."""
    return sync_to_async(function, thread_sensitive=True)(*args)


@dataclass
class PlannedCall(CallOutcome):
    """One call of a round, from its planning to its reading back."""

    # Runs beside other calls, in a pool thread.
    off_thread: bool = False
    # The run was cancelled before this call's turn: it never ran.
    cancelled: bool = False


def _call_through(name):
    async def call_tool(ctx: RunContext[ToolCalls], /, **arguments):
        return await ctx.deps.execute(ctx.tool_call_id)

    call_tool.__name__ = name
    return call_tool


def build_function_toolset(toolset) -> FunctionToolset:
    """The tools of *toolset* as Pydantic AI function tools.

    Arguments are not validated on the way in: the registry validates them
    against the tool's own model and answers a bad call with the failure
    envelope every other failure uses.
    """
    concurrent = toolset.concurrent_names()
    tools = []
    for definition in toolset.get_definitions():
        function = definition["function"]
        name = function["name"]
        tools.append(
            Tool.from_schema(
                _call_through(name),
                name=name,
                description=function.get("description"),
                json_schema=function.get("parameters") or _NO_PARAMETERS,
                takes_ctx=True,
                sequential=name not in concurrent,
            )
        )
    return FunctionToolset(tools)


def _for_model(result):
    """A tool's result as the model is handed it.

    A tool that returns an image says so in a JSON envelope; the model
    gets the picture itself, in whatever form its backend takes one.
    """
    if not isinstance(result, str):
        return result
    try:
        parsed = json.loads(result)
        if not isinstance(parsed, dict) or parsed.get("type") != "image":
            return result
        image = BinaryContent(
            data=base64.b64decode(parsed["data"]),
            media_type=parsed.get("mime_type", "image/webp"),
        )
    except json.JSONDecodeError, KeyError, TypeError, binascii.Error:
        return result
    return [parsed.get("text") or "Here is the image:", image]


class ToolCalls:
    """Plans, runs and reads back the tool calls of one run.

    A round goes through three steps. :meth:`plan_round` ranks the calls
    and settles which are refused and which share threads, before any of
    them starts. :meth:`around_call` and :meth:`execute` run as the library
    reaches each call. :meth:`read_back` returns what came of them, in the
    order the model asked for them.

    *is_cancelled* is read before every call, so a cancellation never
    starts a call that writes memories, schedules messages or bills an
    image. The call position climbs across rounds, never restarting: a tool
    that leaves media for the caller to attach stamps it with the rank the
    model asked for it in.
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
        self._concurrency = max(concurrency, 1)
        self._concurrent_names = toolset.concurrent_names()
        self._user = user
        self._language = get_user_language(user) if user is not None else None
        self._bot = bot
        self._conversation_id = conversation_id
        self._context = context if context is not None else {}
        self._is_cancelled = is_cancelled
        self._policies = list(policies)
        self._observers = list(observers)
        self._position = 0
        self._planned: dict[str, PlannedCall] = {}
        self._pool = None
        self._slots = None

    def open(self):
        """Take the threads and slots of a run. Called on its event loop."""
        self._pool = ThreadPoolExecutor(max_workers=self._concurrency)
        self._slots = asyncio.Semaphore(self._concurrency)

    def close(self):
        if self._pool is not None:
            self._pool.shutdown(wait=False)
            self._pool = None

    def is_cancelled(self) -> bool:
        return bool(self._is_cancelled and self._is_cancelled())

    @property
    def round_cancelled(self) -> bool:
        """Whether a cancellation kept a call of the latest round from running."""
        return any(outcome.cancelled for outcome in self._planned.values())

    def plan_round(self, calls: list[ToolCall]) -> None:
        """Rank the calls of a reply and settle how each will run."""
        planned = []
        for call in calls:
            self._position += 1
            outcome = PlannedCall(call=call, position=self._position)
            refusal = self._refusal(call)
            if refusal is not None:
                # The refusal is what the model reads in place of a result.
                outcome.refusal = outcome.result = refusal
            planned.append(outcome)
        # Alone, a call stays on the task thread: no connection to open.
        for together, group in groupby(
            planned, key=lambda outcome: outcome.call.name in self._concurrent_names
        ):
            pending = [outcome for outcome in group if not outcome.refused]
            if together and self._concurrency > 1 and len(pending) > 1:
                for outcome in pending:
                    outcome.off_thread = True
        self._planned = {outcome.call.id: outcome for outcome in planned}

    def _refusal(self, call):
        for policy in self._policies:
            refusal = policy.refusal(call)
            if refusal is not None:
                logger.info("Refused tool call %s", scrub(call.name))
                return refusal
        return None

    async def around_call(self, ctx, *, call, tool_def, args, handler):
        """Pydantic AI's ``tool_execute`` hook: decide whether a call runs."""
        outcome = self._planned[call.tool_call_id]
        if outcome.refused:
            return outcome.refusal
        async with self._slots if outcome.off_thread else nullcontext():
            # Read as the call gets its turn: the ones before ran for a while.
            if await on_task_thread(self.is_cancelled):
                outcome.cancelled = True
                return _CANCELLED
            await on_task_thread(notify, self._observers, "on_call_start", outcome.call)
            try:
                return await handler(args)
            except Exception as exc:
                outcome.error = exc
                await on_task_thread(notify, self._observers, "on_call_end", outcome)
                raise

    async def execute(self, call_id):
        """Run the handler of a planned call, on the thread it was given."""
        outcome = self._planned[call_id]
        if outcome.off_thread:
            run = sync_to_async(
                self._execute_off_thread, thread_sensitive=False, executor=self._pool
            )
        else:
            run = sync_to_async(self._execute, thread_sensitive=True)
        outcome.result = await run(outcome)
        return _for_model(outcome.result)

    def _execute(self, outcome):
        """Run one call and report its return from wherever it ran.

        The report goes out as soon as the handler returns, so the row of a
        quick call stops spinning without waiting for the slow one it was
        dispatched with.
        """
        set_call_position(outcome.position)
        language = (
            translation.override(self._language) if self._language else nullcontext()
        )
        try:
            with language:
                return self._toolset.execute(
                    outcome.call,
                    user=self._user,
                    bot=self._bot,
                    conversation_id=self._conversation_id,
                    context=self._context,
                )
        finally:
            notify(self._observers, "on_call_return", outcome.call)

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

    def read_back(self, results: ModelRequest) -> list[PlannedCall]:
        """What came of the round's calls, in the order they were asked for.

        *results* is the request the library built from the round. A call
        it answered itself - a tool that does not exist, arguments that are
        not JSON - never reached this class, and its answer is read there.
        Calls a cancellation kept from running are left out.
        """
        answered = {
            part.tool_call_id: part
            for part in results.parts
            if isinstance(part, ToolReturnPart)
        }
        outcomes = []
        for call_id, outcome in self._planned.items():
            if outcome.cancelled:
                continue
            if outcome.result is None:
                part = answered.get(call_id)
                if part is None:
                    continue
                outcome.result = tool_failure(part.model_response_str())
            outcomes.append(outcome)
            notify(self._observers, "on_call_end", outcome)
        return outcomes
