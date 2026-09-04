"""The loop: ask the model, run what it asked for, ask again.

Everything the loop does besides that is somebody else's: the dispatcher
runs the calls, the policies refuse some, the observers watch, the record
writes it down. What remains here is when the loop stops, and why.
"""

from dataclasses import dataclass, replace
from enum import Enum

from workspace.ai.services.llm import build_tool_content

from .model import ModelResponse
from .observers import notify


class StopReason(Enum):
    ANSWERED = "answered"
    # A tool asked the round to halt and wait for the user (a question, a
    # confirmation): the reply is the round's response, and the loop
    # resumes on the next message.
    AWAITING_USER = "awaiting_user"
    CANCELLED = "cancelled"
    # Every call of a round was a refused duplicate: the model was circling.
    REPEAT_LOOP = "repeat_loop"
    ROUND_CAP = "round_cap"


@dataclass
class RunResult:
    response: ModelResponse
    context: dict
    rounds: list
    tool_data: list | None
    stop: StopReason


class AgentRunner:
    """Runs one reply: a conversation in, a response and its records out.

    *context* is the dict tools write side effects into (images, a pending
    question, messages queued for the user). The runner reads one key of
    it, ``stop_after_round``, and the caller reads the rest off the result.

    *is_cancelled* is re-read at every round boundary, on top of the
    dispatcher's reads before each call, so a cancellation that lands
    during the tools does not buy one more model call.
    """

    def __init__(
        self,
        *,
        model,
        toolset,
        dispatcher,
        record,
        max_rounds,
        observers=(),
        is_cancelled=None,
        context=None,
    ):
        self._model = model
        self._toolset = toolset
        self._dispatcher = dispatcher
        self._record = record
        self._max_rounds = max_rounds
        self._observers = list(observers)
        self._is_cancelled = is_cancelled
        self.context = context if context is not None else {}

    def run(self, messages: list[dict]) -> RunResult:
        tools = self._toolset.get_definitions()
        response = self._model.complete(messages, tools=tools)
        for _ in range(self._max_rounds):
            if not response.tool_calls:
                self._record.reply(response)
                return self._finish(response, StopReason.ANSWERED)
            messages.append(response.as_assistant_message())
            self._record.assistant_turn(response)
            round_outcome = self._dispatcher.run_round(response.tool_calls)
            for outcome in round_outcome.outcomes:
                content = build_tool_content(outcome.result)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": outcome.call.id,
                        "content": content,
                    }
                )
                self._record.tool_result(outcome, content)
            if round_outcome.cancelled or self._cancelled():
                self._record.flag(cancelled=True)
                return self._finish(response, StopReason.CANCELLED)
            if not round_outcome.executed:
                # Let the model answer from what it already gathered instead
                # of spending the remaining rounds.
                self._record.flag(repeat_loop_stopped=True)
                response = self._model.complete(messages)
                self._record.reply(response)
                return self._finish(response, StopReason.REPEAT_LOOP)
            if self.context.get("stop_after_round"):
                self._record.flag(terminated_by_tool=True)
                return self._finish(response, StopReason.AWAITING_USER)
            response = self._model.complete(messages, tools=tools)
        if not response.tool_calls:
            self._record.reply(response)
            return self._finish(response, StopReason.ANSWERED)
        # The last response is another tool call that will never run, so
        # returning it hands the caller a reply with no text: re-ask without
        # tools to turn what was gathered into an answer.
        self._record.reply(response, round_cap_reached=True)
        response = self._model.complete(messages)
        self._record.reply(response)
        return self._finish(response, StopReason.ROUND_CAP)

    def retry_final(self, messages: list[dict], run: RunResult) -> RunResult:
        """Ask once more for a text answer, running no tool.

        For a run that ended on an empty reply: rerunning the loop would
        execute every tool again and trigger side effects twice, while
        *messages*, which the run already extended with every call and
        result, still carries the context to answer from.
        """
        response = self._model.complete(messages)
        self._record.reply(response)
        return replace(run, response=response)

    def _cancelled(self):
        return bool(self._is_cancelled and self._is_cancelled())

    def _finish(self, response, stop):
        run = RunResult(
            response=response,
            context=self.context,
            rounds=self._record.rounds,
            tool_data=self._record.tool_data or None,
            stop=stop,
        )
        notify(self._observers, "on_stop", run)
        return run
