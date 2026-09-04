"""Hooks that watch a run without steering it.

An observer hears of each call starting, returning and being read back,
and of the run ending. The base class does nothing, so an observer only
writes the methods it needs. None of them may raise: a lost step or a
missed counter must never take down the reply it reports on.

Three moments, on three threads:

- ``on_call_start`` - main thread, in call order, before the call is
  dispatched. Refused calls are never announced.
- ``on_call_return`` - the thread the call ran on, the moment its handler
  returned or raised.
- ``on_call_end`` - main thread, in call order, once the whole batch is
  back. Refused calls are read back here too.
"""

from django.conf import settings

from workspace.ai.metrics import AI_TOOL_CALLS, AI_TOOL_LOOP_STOPS, AI_TOOL_ROUNDS
from workspace.ai.services.stream_steps import (
    notify_tool_step,
    notify_tool_step_done,
    step_recipients,
)

from .runner import StopReason

# Tool handlers report a failure to the model as a plain string rather than
# an exception, so these prefixes are the only signal a call went wrong.
_FAILED_RESULT_PREFIXES = ("error:", "unknown tool:")


class Observer:
    def on_call_start(self, call):
        pass

    def on_call_return(self, call):
        pass

    def on_call_end(self, outcome):
        pass

    def on_stop(self, run):
        pass


class StreamStepsObserver(Observer):
    """Shows each call to the conversation's members while it runs.

    Membership is re-read per event, not snapshotted for the run: a member
    who leaves mid-run must stop receiving progress from a conversation
    they are no longer in. A run outside a conversation shows nobody
    anything.
    """

    def __init__(self, conversation_id, bot_user):
        self._conversation_id = conversation_id
        self._bot_user = bot_user

    def _recipients(self):
        return step_recipients(self._conversation_id, self._bot_user)

    def on_call_start(self, call):
        if self._conversation_id:
            notify_tool_step(self._recipients(), self._conversation_id, call)

    def on_call_return(self, call):
        if self._conversation_id:
            notify_tool_step_done(self._recipients(), self._conversation_id, call)


def _result_status(result):
    text = result if isinstance(result, str) else ""
    return "error" if text.strip().lower().startswith(_FAILED_RESULT_PREFIXES) else "ok"


class MetricsObserver(Observer):
    """Counts calls, rounds and early stops for Prometheus."""

    def __init__(self, toolset, model):
        # A model can invent a tool name, and that name reaches a metric
        # label: anything the registry doesn't know is folded into one series.
        self._known = {t["function"]["name"] for t in toolset.get_definitions()}
        self._model = model or settings.AI_MODEL

    def _tool_label(self, call):
        return call.name if call.name in self._known else "unknown"

    def on_call_end(self, outcome):
        tool = self._tool_label(outcome.call)
        if outcome.refused:
            status = "repeat"
        elif outcome.error is not None:
            status = "error"
        else:
            status = _result_status(outcome.result)
        AI_TOOL_CALLS.labels(tool=tool, status=status).inc()

    def on_stop(self, run):
        if run.stop in (StopReason.ROUND_CAP, StopReason.REPEAT_LOOP):
            AI_TOOL_LOOP_STOPS.labels(reason=run.stop.value).inc()
        if run.stop is not StopReason.CANCELLED:
            # A cancelled run stopped for a reason unrelated to how the model
            # works, so counting it would flatten the distribution it measures.
            AI_TOOL_ROUNDS.labels(model=self._model).observe(len(run.tool_data or []))
