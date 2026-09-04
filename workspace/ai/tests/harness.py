"""Stand-ins for testing the harness without a backend or a registry.

``ScriptedModel`` replays the replies a test wrote down and records each
request; ``StubToolset`` runs handlers for real (in a pool thread when the
dispatcher batches them) and records when each call started and ended.
``build_runner`` wires them the way ``build_bot_runner`` wires the real
parts, with every limit explicit.
"""

import threading

from workspace.ai.harness.dispatch import Dispatcher
from workspace.ai.harness.model import ModelResponse, ToolCall
from workspace.ai.harness.policies import RepeatGuard
from workspace.ai.harness.record import RunRecord
from workspace.ai.harness.runner import AgentRunner


def call(call_id, name="search", arguments="{}"):
    return ToolCall(id=call_id, name=name, arguments=arguments)


def reply(content="", *, thinking="", model="x"):
    """A reply that asks for nothing."""
    return ModelResponse(
        content=content,
        thinking=thinking,
        model=model,
        prompt_tokens=0,
        completion_tokens=0,
    )


def tool_reply(*calls, content="", thinking="", model="x"):
    """A reply that asks for *calls*."""
    return ModelResponse(
        content=content,
        thinking=thinking,
        tool_calls=list(calls),
        model=model,
        prompt_tokens=0,
        completion_tokens=0,
    )


class ScriptedModel:
    """Replays *script*: a list of replies, or a callable producing them.

    A list is consumed in order; with ``repeat=True`` its last reply is
    given again for every request past the end, the way a stuck model
    keeps asking for the same call. ``requests`` keeps each request as
    ``(messages, tools)``, the messages copied at the time of the call.
    """

    def __init__(self, script, *, repeat=False):
        self._script = script
        self._repeat = repeat
        self.requests = []

    def complete(self, messages, *, tools=None):
        self.requests.append((list(messages), tools))
        if callable(self._script):
            return self._script(messages, tools)
        index = len(self.requests) - 1
        if index >= len(self._script):
            if not self._repeat:
                raise AssertionError("the script has no reply left for this request")
            index = len(self._script) - 1
        return self._script[index]

    @property
    def last_tools(self):
        return self.requests[-1][1]


class StubToolset:
    """Runs *handler* for every call and records the spans of each.

    *handler* takes the call and the run's context. *concurrent* names the
    tools the dispatcher may run together; *definitions* the tools the
    model is offered, which is what the metrics fold unknown names against.
    """

    def __init__(self, concurrent=(), handler=None, definitions=(), describe=None):
        self._concurrent = frozenset(concurrent)
        self._handler = handler or (lambda tc, ctx: f"result {tc.id}")
        self._definitions = tuple(definitions)
        self._describe = describe
        self.spans = []
        self.in_flight = 0
        self.peak_in_flight = 0
        self._lock = threading.Lock()

    def get_definitions(self):
        return [
            {"type": "function", "function": {"name": name, "parameters": {}}}
            for name in self._definitions
        ]

    def concurrent_names(self):
        return self._concurrent

    def describe_call(self, name, raw_arguments, max_len=120):
        if self._describe:
            return self._describe(name, raw_arguments)
        return name

    def execute(self, tool_call, user, bot, conversation_id=None, context=None):
        self._enter(tool_call.id)
        try:
            return self._handler(tool_call, context if context is not None else {})
        finally:
            self._leave(tool_call.id)

    @property
    def executed(self):
        """Ids of the calls that ran, in the order they started."""
        return [call_id for call_id, event in self.spans if event == "start"]

    def _enter(self, call_id):
        with self._lock:
            self.spans.append((call_id, "start"))
            self.in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self.in_flight)

    def _leave(self, call_id):
        with self._lock:
            self.in_flight -= 1
            self.spans.append((call_id, "end"))


def build_runner(
    model,
    toolset,
    *,
    user=None,
    bot=None,
    conversation_id=None,
    is_cancelled=None,
    context=None,
    observers=(),
    max_rounds=10,
    max_identical_calls=3,
    concurrency=4,
    task_max_chars=2000,
    store_max_chars=12000,
):
    context = context if context is not None else {}
    dispatcher = Dispatcher(
        toolset,
        concurrency=concurrency,
        user=user,
        bot=bot,
        conversation_id=conversation_id,
        context=context,
        is_cancelled=is_cancelled,
        policies=[RepeatGuard(max_identical_calls)],
        observers=observers,
    )
    record = RunRecord(
        toolset, task_max_chars=task_max_chars, store_max_chars=store_max_chars
    )
    return AgentRunner(
        model=model,
        toolset=toolset,
        dispatcher=dispatcher,
        record=record,
        max_rounds=max_rounds,
        observers=observers,
        is_cancelled=is_cancelled,
        context=context,
    )


def tool_messages(messages):
    return [(m["tool_call_id"], m["content"]) for m in messages if m["role"] == "tool"]
