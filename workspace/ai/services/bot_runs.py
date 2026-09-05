"""Assembling the harness for one bot reply.

The three ways a bot comes to answer - a message in the chat, a scheduled
message, an agent goal check-in - run the same loop with the same tools,
the same limits and the same watchers. This is the one place they are put
together, so a task holds a runner and nothing of how it was built.
"""

from django.conf import settings

from workspace.ai.harness.dispatch import Dispatcher
from workspace.ai.harness.model import LLMModel
from workspace.ai.harness.observers import MetricsObserver, StreamStepsObserver
from workspace.ai.harness.policies import RepeatGuard
from workspace.ai.harness.record import RunRecord
from workspace.ai.harness.runner import AgentRunner


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
    model = LLMModel(bot_profile.get_model())
    observers = [
        StreamStepsObserver(conversation_id, bot_user),
        MetricsObserver(tool_registry, model.name),
    ]
    dispatcher = Dispatcher(
        tool_registry,
        concurrency=settings.AI_TOOL_CONCURRENCY,
        user=human_user,
        bot=bot_user,
        conversation_id=conversation_id,
        context=context,
        is_cancelled=is_cancelled,
        policies=[RepeatGuard(settings.AI_MAX_IDENTICAL_TOOL_CALLS)],
        observers=observers,
    )
    record = RunRecord(
        tool_registry,
        task_max_chars=settings.AI_TOOL_RESULT_TASK_MAX_CHARS,
        store_max_chars=settings.AI_TOOL_RESULT_STORE_MAX_CHARS,
    )
    return AgentRunner(
        model=model,
        toolset=tool_registry,
        dispatcher=dispatcher,
        record=record,
        max_rounds=settings.AI_MAX_TOOL_ROUNDS,
        observers=observers,
        is_cancelled=is_cancelled,
        context=context,
    )
