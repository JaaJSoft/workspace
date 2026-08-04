"""Per-user cache mailbox for ephemeral bot progress steps.

While a bot response is being generated in a Celery worker, each tool
execution pushes a small "step" envelope (icon + label + detail) to every
active member of the conversation, then wakes their SSE stream via
``notify_sse``. Mirrors the call-signaling mailbox pattern: nothing here
is durable — a lost step only means the progress label lags until the
next one (or the final message) arrives, so failures must never abort
the generation they report on.
"""

import json
import logging

from workspace.common.uuids import uuid_v7_or_v4
from workspace.core.sse_registry import notify_sse

logger = logging.getLogger(__name__)

STEP_EVENT_TTL = 120  # seconds; a step is stale once the response lands
MAX_QUEUE = 50  # backstop against an unbounded mailbox if a client never drains

# Dedicated provider slug: waking the heavy "chat" provider (a dozen DB
# queries per dirty poll) for every tool execution would be wasteful.
PROVIDER_SLUG = "ai_stream"

# Detail strings come from LLM-generated tool arguments; keep them short.
MAX_DETAIL_LEN = 200


def _events_key(user_id):
    return f"ai:step_events:{user_id}"


def step_recipients(conversation_id, bot_user):
    """Return user ids of active members of *conversation_id*, minus the bot."""
    from workspace.chat.models import ConversationMember

    return list(
        ConversationMember.objects.filter(
            conversation_id=conversation_id,
            left_at__isnull=True,
        )
        .exclude(user=bot_user)
        .values_list("user_id", flat=True)
    )


def _enqueue(user_id, step):
    from django.core.cache import cache

    key = _events_key(user_id)
    queue = cache.get(key) or []
    queue.append({"id": str(uuid_v7_or_v4()), "data": step})
    if len(queue) > MAX_QUEUE:
        queue = queue[-MAX_QUEUE:]
    cache.set(key, queue, STEP_EVENT_TTL)


def drain_steps(user_id):
    """Return and clear all queued step envelopes for *user_id*."""
    from django.core.cache import cache

    key = _events_key(user_id)
    queue = cache.get(key)
    if not queue:
        return []
    cache.delete(key)
    return queue


def notify_tool_step(recipient_ids, conversation_id, tool_call):
    """Push a "bot is running <tool>" step to every recipient's stream.

    Never raises: a broken cache/Redis or malformed tool arguments must
    not take down the response generation.
    """
    if not recipient_ids:
        return
    try:
        from workspace.ai.tool_registry import tool_registry

        name = tool_call.function.name
        badge = tool_registry.get_badge(name)
        try:
            args = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError, TypeError:
            args = {}
        detail = tool_registry.get_detail(name, args) if isinstance(args, dict) else ""
        step = {
            "conversation_id": str(conversation_id),
            "icon": badge["icon"],
            "label": badge["label"],
            "detail": str(detail)[:MAX_DETAIL_LEN],
        }
        for user_id in recipient_ids:
            _enqueue(user_id, step)
        for user_id in recipient_ids:
            notify_sse(PROVIDER_SLUG, user_id)
    except Exception:
        logger.exception("Failed to push bot step event")
