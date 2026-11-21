"""Stand-ins for testing the Pydantic AI runtime without a backend.

``scripted`` turns a :class:`~workspace.ai.tests.harness.ScriptedModel`
into the model a Pydantic AI agent talks to, so a test writes its replies
the way the tests of the other runner do. ``build_runner`` wires the
runner the way ``build_bot_runner`` does, with every limit explicit.
"""

from contextlib import asynccontextmanager

from pydantic_ai.messages import (
    ModelRequest,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.messages import ModelResponse as AgentResponse
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from workspace.ai.harness.policies import RepeatGuard
from workspace.ai.harness.pydantic_runtime.runner import PydanticAIRunner
from workspace.ai.harness.pydantic_runtime.tool_calls import ToolCalls
from workspace.ai.harness.record import RunRecord


def _as_agent_response(response) -> AgentResponse:
    parts = []
    if response.thinking:
        parts.append(ThinkingPart(response.thinking))
    if response.content:
        parts.append(TextPart(response.content))
    parts.extend(
        ToolCallPart(call.name, call.arguments, tool_call_id=call.id)
        for call in response.tool_calls
    )
    return AgentResponse(
        parts=parts,
        model_name=response.model,
        usage=RequestUsage(
            input_tokens=response.prompt_tokens or 0,
            output_tokens=response.completion_tokens or 0,
        ),
    )


def scripted(model) -> FunctionModel:
    """*model*'s script, served to a Pydantic AI agent.

    ``model.requests`` keeps each request as ``(messages, tools)``: the
    Pydantic AI messages sent, and the names of the tools on offer, or
    None when the request offered none.
    """

    def answer(messages, info):
        tools = [tool.name for tool in info.function_tools] or None
        return _as_agent_response(model.complete(messages, tools=tools))

    return FunctionModel(answer)


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
    function_model = scripted(model)

    @asynccontextmanager
    async def open_model():
        yield function_model

    return PydanticAIRunner(
        open_model=open_model,
        toolset=toolset,
        tool_calls=ToolCalls(
            toolset,
            concurrency=concurrency,
            user=user,
            bot=bot,
            conversation_id=conversation_id,
            context=context,
            is_cancelled=is_cancelled,
            policies=[RepeatGuard(max_identical_calls)],
            observers=observers,
        ),
        record=RunRecord(
            toolset, task_max_chars=task_max_chars, store_max_chars=store_max_chars
        ),
        max_rounds=max_rounds,
        observers=observers,
        context=context,
    )


def tool_results(messages):
    """``(call id, content)`` of every tool result in Pydantic AI *messages*."""
    return [
        (part.tool_call_id, part.content)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
