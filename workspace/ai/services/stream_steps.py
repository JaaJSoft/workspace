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

from workspace.chat.services.tool_calls import display_args
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


def _queue(user_id):
    from django.core.cache import cache

    return cache.get(_events_key(user_id)) or []


def latest_step_id(user_id):
    """Id of the last queued step, or None when the mailbox is empty."""
    queue = _queue(user_id)
    return queue[-1]["id"] if queue else None


def steps_after(user_id, last_id):
    """Return the step envelopes queued after *last_id*.

    Reads without consuming: a user can hold several SSE connections (two
    tabs, or an EventSource reconnect overlapping the connection it
    replaces) and every one of them must see every step. Each connection
    tracks its own cursor instead; entries fall out on their own via
    STEP_EVENT_TTL and MAX_QUEUE.
    """
    queue = _queue(user_id)
    if last_id is None:
        return queue
    for index in range(len(queue) - 1, -1, -1):
        if queue[index]["id"] == last_id:
            return queue[index + 1 :]
    # Cursor aged out of the capped window: resync on what is still there.
    return queue


def notify_tool_step(recipient_ids, conversation_id, tool_call):
    """Push a "bot is running <tool>" step to every recipient's stream.

    Never raises: a broken cache/Redis or malformed tool arguments must
    not take down the response generation.
    """
    if not recipient_ids:
        return
    try:
        from django.template.loader import render_to_string

        from workspace.ai.tool_registry import tool_registry

        name = tool_call.function.name
        badge = tool_registry.get_badge(name)
        raw_args = tool_call.function.arguments or ""
        try:
            parsed = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError, TypeError:
            parsed = None
        detail = (
            tool_registry.get_detail(name, parsed) if isinstance(parsed, dict) else ""
        )
        # Render the final message's tool timeline row itself (auto-escaped
        # there and here), so a live step is that row minus the result the
        # tool has not produced yet, and a tool's presentation has one source
        # of truth.
        html = render_to_string(
            "chat/ui/partials/_tool_call_row.html",
            {
                "call": {
                    "icon": badge["icon"],
                    "label": badge["label"],
                    "detail": str(detail)[:MAX_DETAIL_LEN],
                    "args": display_args(parsed),
                    "args_raw": raw_args if parsed is None else "",
                }
            },
        )
        step = {
            "conversation_id": str(conversation_id),
            "html": html,
        }
        for user_id in recipient_ids:
            _enqueue(user_id, step)
        for user_id in recipient_ids:
            notify_sse(PROVIDER_SLUG, user_id)
    except Exception:
        logger.exception("Failed to push bot step event")
