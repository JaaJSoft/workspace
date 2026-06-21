"""Per-user cache mailbox for ephemeral call events (lifecycle + WebRTC signaling).

Mirrors the typing-indicator pattern: writes land in the cache and a
``notify_sse`` ping wakes the recipient's SSE poll, which drains the mailbox.
Nothing here is durable - the source of truth for call state is the DB.
"""

from workspace.common.uuids import uuid_v7_or_v4
from workspace.core.sse_registry import notify_sse

CALL_EVENT_TTL = 60  # seconds; events are consumed within one poll cycle
MAX_QUEUE = 200  # backstop against an unbounded mailbox if a client never drains


def _events_key(user_id):
    return f"chat:call_events:{user_id}"


def enqueue_event(user_id, event, data):
    """Append an event envelope to *user_id*'s mailbox. Returns the envelope id.

    Does not notify; callers batch the ``notify_sse`` after enqueuing to every
    recipient so a single fan-out wakes each user once.
    """
    from django.core.cache import cache

    key = _events_key(user_id)
    envelope_id = str(uuid_v7_or_v4())
    queue = cache.get(key) or []
    queue.append({"id": envelope_id, "event": event, "data": data})
    if len(queue) > MAX_QUEUE:
        queue = queue[-MAX_QUEUE:]
    cache.set(key, queue, CALL_EVENT_TTL)
    return envelope_id


def drain_events(user_id):
    """Return and clear all queued events for *user_id*."""
    from django.core.cache import cache

    key = _events_key(user_id)
    queue = cache.get(key)
    if not queue:
        return []
    cache.delete(key)
    return queue


def send_signal(session_id, to_user_id, from_user_id, signal):
    """Deliver a WebRTC signal envelope to a single peer and wake their stream."""
    envelope_id = enqueue_event(
        to_user_id,
        "call_signal",
        {
            "session_id": str(session_id),
            "from_user_id": from_user_id,
            "signal": signal,
        },
    )
    notify_sse("chat", to_user_id)
    return envelope_id


DIAGNOSTIC_LANES = ("to_caller", "to_callee")


def send_diagnostic_signal(user_id, lane, signal, run_id):
    """Echo a diagnostic WebRTC signal back to its own sender, then wake their stream.

    Used by the call connection diagnostic: two local peer connections in the
    same browser exchange SDP/ICE through the server, so this delivers the
    signal to the originating user (not a remote peer). The ``lane`` tells the
    client which of its two local connections the echo is destined for.
    """
    envelope_id = enqueue_event(
        user_id,
        "call_diagnostic_signal",
        {"lane": lane, "signal": signal, "run_id": run_id},
    )
    notify_sse("chat", user_id)
    return envelope_id
