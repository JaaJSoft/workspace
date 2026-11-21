"""The loop on Pydantic AI: its agent asks the model and runs the tools.

The agent is stepped by hand, one node at a time, because when a reply
ends is still ours to say: on an answer, on a tool waiting for the user,
on a cancellation, on a model going in circles, on the round cap. The
answer asked for once the tools are taken away is a plain model request
on the conversation the agent built.

A run is synchronous to its caller. Pydantic AI is not, so the run gets an
event loop of its own for as long as it lasts.
"""

import time
from dataclasses import replace

import pydantic_ai
from asgiref.sync import async_to_sync
from django.conf import settings
from pydantic_ai import Agent, direct
from pydantic_ai.capabilities import Hooks
from pydantic_ai.messages import TextPart, ToolCallPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.usage import UsageLimits

from workspace.ai.harness.observers import notify
from workspace.ai.harness.runner import RunResult, StopReason
from workspace.ai.metrics import AI_REQUEST_DURATION, AI_TOKENS
from workspace.ai.services.llm import build_tool_content, extract_text_tool_calls

from .messages import to_model_messages, to_model_response
from .tool_calls import build_function_toolset, on_task_thread

# Printed into the worker's log on the first run of a process otherwise.
pydantic_ai.BANNER_ENABLED = False

# The library fails a run whose model keeps calling a tool wrongly. Here the
# rounds and the repeat guard bound a reply, and a wrong call is read back.
_UNBOUNDED_RETRIES = 1_000_000


async def _read_text_tool_calls(ctx, *, request_context, response):
    """Pydantic AI's ``after_model_request`` hook: calls written out as text.

    A backend without native function calling still gets to use tools when
    the model writes its calls as JSON. A request that offered none is
    never read that way: it is the run asking for a final answer, and JSON
    in it is the answer.
    """
    if not request_context.model_request_parameters.function_tools:
        return response
    if any(isinstance(part, ToolCallPart) for part in response.parts):
        return response
    text = "".join(
        part.content for part in response.parts if isinstance(part, TextPart)
    )
    calls, remaining = extract_text_tool_calls(text) if text else (None, text)
    if not calls:
        return response
    parts = [part for part in response.parts if not isinstance(part, TextPart)]
    if remaining:
        parts.append(TextPart(remaining))
    parts.extend(ToolCallPart(name, arguments) for name, arguments in calls)
    return replace(response, parts=parts)


class PydanticAIRunner:
    """Runs one reply on a Pydantic AI agent.

    Takes and returns what :class:`~workspace.ai.harness.runner.AgentRunner`
    does, so a task holds either without knowing which. *open_model* is
    called for each stretch of the run that talks to the backend, and
    returns an async context manager yielding the model.
    """

    def __init__(
        self,
        *,
        open_model,
        toolset,
        tool_calls,
        record,
        max_rounds,
        model_settings=None,
        observers=(),
        context=None,
    ):
        self._open_model = open_model
        self._function_toolset = build_function_toolset(toolset)
        self._tool_calls = tool_calls
        self._record = record
        self._max_rounds = max_rounds
        self._model_settings = model_settings
        self._observers = list(observers)
        self.context = context if context is not None else {}
        # What a tool-less answer is asked on: all but the reply the run ended on.
        self._history = []

    def run(self, messages: list[dict]) -> RunResult:
        response, stop = async_to_sync(self._run)(to_model_messages(messages))
        run = RunResult(
            response=response,
            context=self.context,
            rounds=self._record.rounds,
            tool_data=self._record.tool_data or None,
            stop=stop,
            usage=self._record.usage,
        )
        notify(self._observers, "on_stop", run)
        return run

    def retry_final(self, messages: list[dict], run: RunResult) -> RunResult:
        """Ask once more for a text answer, running no tool.

        For a run that ended on an empty reply: rerunning the loop would
        execute every tool again and trigger side effects twice, while the
        conversation the run built still carries the context to answer
        from. *messages* is not read: that conversation is kept here.
        """
        response = async_to_sync(self._answer_again)()
        self._record.reply(response)
        return replace(run, response=response, usage=self._record.usage)

    async def _answer_again(self):
        async with self._open_model() as model:
            return await self._answer(model)

    async def _run(self, history):
        self._tool_calls.open()
        try:
            async with self._open_model() as model:
                return await self._loop(model, history)
        finally:
            self._tool_calls.close()

    async def _loop(self, model, history):
        agent = Agent(
            model,
            toolsets=[self._function_toolset],
            capabilities=[
                Hooks(
                    after_model_request=_read_text_tool_calls,
                    tool_execute=self._tool_calls.around_call,
                )
            ],
            model_settings=self._model_settings,
            retries=_UNBOUNDED_RETRIES,
        )
        record = self._record
        rounds = 0
        async with agent.iter(
            message_history=history,
            deps=self._tool_calls,
            usage_limits=UsageLimits(request_limit=None),
        ) as agent_run:
            # The first node only hands the conversation to the first request.
            node = await agent_run.next(agent_run.next_node)
            while True:
                node, response = await self._ask(agent_run, node, model)
                self._history = agent_run.all_messages()[:-1]
                if not response.tool_calls:
                    record.reply(response)
                    return response, StopReason.ANSWERED
                if rounds == self._max_rounds:
                    # A call that will never run is no answer: ask for one
                    # without tools, from what was gathered.
                    record.reply(response, round_cap_reached=True)
                    response = await self._answer(model)
                    record.reply(response)
                    return response, StopReason.ROUND_CAP
                rounds += 1
                record.assistant_turn(response)
                await on_task_thread(self._tool_calls.plan_round, response.tool_calls)
                node = await agent_run.next(node)
                outcomes = await on_task_thread(
                    self._tool_calls.read_back, node.request
                )
                for outcome in outcomes:
                    record.tool_result(outcome, build_tool_content(outcome.result))
                self._history = [*agent_run.all_messages(), node.request]
                # A cancellation during the tools buys no further model call.
                if self._tool_calls.round_cancelled or await on_task_thread(
                    self._tool_calls.is_cancelled
                ):
                    record.flag(cancelled=True)
                    return response, StopReason.CANCELLED
                if all(outcome.refused for outcome in outcomes):
                    # Answer from what was gathered, not the rounds left.
                    record.flag(repeat_loop_stopped=True)
                    response = await self._answer(model)
                    record.reply(response)
                    return response, StopReason.REPEAT_LOOP
                if self.context.get("stop_after_round"):
                    record.flag(terminated_by_tool=True)
                    return response, StopReason.AWAITING_USER

    async def _ask(self, agent_run, node, model):
        """Run a model request node: the reply, and the node that holds it."""
        started = time.monotonic()
        try:
            node = await agent_run.next(node)
        except Exception:
            _observe_failure(model, started)
            raise
        return node, _observed(node.model_response, started)

    async def _answer(self, model):
        """Ask for a reply with no tool on offer."""
        started = time.monotonic()
        try:
            response = await direct.model_request(
                model,
                self._history,
                model_settings=self._model_settings,
                model_request_parameters=ModelRequestParameters(),
            )
        except Exception:
            _observe_failure(model, started)
            raise
        return _observed(response, started)


def _observe_failure(model, started):
    AI_REQUEST_DURATION.labels(
        model=getattr(model, "model_name", "") or settings.AI_MODEL, status="error"
    ).observe(time.monotonic() - started)


def _observed(agent_response, started):
    """The reply as the tasks read it, counted for Prometheus."""
    response = to_model_response(agent_response, duration=time.monotonic() - started)
    model = response.model or settings.AI_MODEL
    AI_REQUEST_DURATION.labels(model=model, status="ok").observe(response.duration)
    if response.prompt_tokens:
        AI_TOKENS.labels(model=model, kind="prompt").inc(response.prompt_tokens)
    if response.completion_tokens:
        AI_TOKENS.labels(model=model, kind="completion").inc(response.completion_tokens)
    return response
