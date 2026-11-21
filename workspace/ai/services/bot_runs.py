"""Assembling the harness for one bot reply.

The three ways a bot comes to answer - a message in the chat, a scheduled
message, an agent goal check-in - run the same loop with the same tools,
the same limits and the same watchers. This is the one place they are put
together, so a task holds a runner and nothing of how it was built.

Two runtimes can run that loop, chosen by ``AI_AGENT_RUNTIME``. Both take
the same parts and return the same result, so nothing past this module
knows which one answered.
"""

from functools import partial

from django.conf import settings

from workspace.ai.harness.dispatch import Dispatcher
from workspace.ai.harness.model import LLMModel
from workspace.ai.harness.observers import MetricsObserver, StreamStepsObserver
from workspace.ai.harness.policies import RepeatGuard
from workspace.ai.harness.record import RunRecord
from workspace.ai.harness.runner import AgentRunner

PYDANTIC_AI_RUNTIME = "pydantic_ai"


def build_bot_runner(
    bot_profile,
    human_user,
    bot_user,
    conversation_id,
    *,
    is_cancelled=None,
    context=None,
):
    """A runner for *bot_profile* answering in *conversation_id*.

    *context* seeds the dict tools write into, letting the caller mark the
    kind of run for tools whose behavior depends on it (an agent goal
    check-in, where send_user_message is only valid because the final text
    is discarded). *is_cancelled* is read before every tool and every
    model call after the first.
    """
    from workspace.ai.tool_registry import tool_registry

    context = context if context is not None else {}
    model_name = bot_profile.get_model()
    observers = [
        StreamStepsObserver(conversation_id, bot_user),
        MetricsObserver(tool_registry, model_name),
    ]
    calls = {
        "concurrency": settings.AI_TOOL_CONCURRENCY,
        "user": human_user,
        "bot": bot_user,
        "conversation_id": conversation_id,
        "context": context,
        "is_cancelled": is_cancelled,
        "policies": [RepeatGuard(settings.AI_MAX_IDENTICAL_TOOL_CALLS)],
        "observers": observers,
    }
    record = RunRecord(
        tool_registry,
        task_max_chars=settings.AI_TOOL_RESULT_TASK_MAX_CHARS,
        store_max_chars=settings.AI_TOOL_RESULT_STORE_MAX_CHARS,
    )
    if settings.AI_AGENT_RUNTIME == PYDANTIC_AI_RUNTIME:
        # Imported here: a web worker loads this module and never runs a bot.
        from workspace.ai.harness.pydantic_runtime.model import (
            model_settings,
            open_model,
        )
        from workspace.ai.harness.pydantic_runtime.runner import PydanticAIRunner
        from workspace.ai.harness.pydantic_runtime.tool_calls import ToolCalls

        return PydanticAIRunner(
            open_model=partial(open_model, model_name),
            toolset=tool_registry,
            tool_calls=ToolCalls(tool_registry, **calls),
            record=record,
            max_rounds=settings.AI_MAX_TOOL_ROUNDS,
            model_settings=model_settings(),
            observers=observers,
            context=context,
        )
    return AgentRunner(
        model=LLMModel(model_name),
        toolset=tool_registry,
        dispatcher=Dispatcher(tool_registry, **calls),
        record=record,
        max_rounds=settings.AI_MAX_TOOL_ROUNDS,
        observers=observers,
        is_cancelled=is_cancelled,
        context=context,
    )
