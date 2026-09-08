"""Per-participant cache mailbox for ephemeral call events (lifecycle + WebRTC signaling).

Mirrors the typing-indicator pattern: writes land in the cache and a
``notify_participant`` ping wakes the recipient's stream, which drains the
mailbox. Nothing here is durable - the source of truth for call state is the DB.

A mailbox is addressed by a participant key (``u:<user_id>`` or
``g:<guest_uuid>``, see :mod:`workspace.chat.services.participant_keys`) so a
member and a meeting guest share one delivery mechanism.
"""

from workspace.common.uuids import uuid_v7_or_v4
from workspace.core.sse_registry import notify_sse

from .participant_keys import user_id_from_key, user_key

CALL_EVENT_TTL = 60  # seconds; events are consumed within one poll cycle
MAX_QUEUE = 200  # backstop against an unbounded mailbox if a client never drains


def _events_key(participant_key):
    return f"chat:call_events:{participant_key}"


def enqueue_event(participant_key, event, data):
    """Append an event envelope to *participant_key*'s mailbox. Returns its id.

    Does not notify; callers batch the wake-up after enqueuing to every
    recipient so a single fan-out wakes each participant once.
    """
    from django.core.cache import cache

    key = _events_key(participant_key)
    envelope_id = str(uuid_v7_or_v4())
    queue = cache.get(key) or []
    queue.append({"id": envelope_id, "event": event, "data": data})
    if len(queue) > MAX_QUEUE:
        queue = queue[-MAX_QUEUE:]
    cache.set(key, queue, CALL_EVENT_TTL)
    return envelope_id


def drain_events(participant_key):
    """Return and clear all queued events for *participant_key*."""
    from django.core.cache import cache

    key = _events_key(participant_key)
    queue = cache.get(key)
    if not queue:
        return []
    cache.delete(key)
    return queue


def notify_participant(participant_key):
    """Wake the stream that drains *participant_key*'s mailbox.

    A member is woken through the global SSE stream. A guest key is a no-op
    until PR 3 gives guests their own transport; waking nothing is correct
    rather than merely harmless, since there is no stream to wake.
    """
    user_id = user_id_from_key(participant_key)
    if user_id is not None:
        notify_sse("chat", user_id)


def send_signal(session_id, to_participant, from_participant, signal):
    """Deliver a WebRTC signal envelope to a single peer and wake their stream."""
    envelope_id = enqueue_event(
        to_participant,
        "call_signal",
        {
            "session_id": str(session_id),
            "from_participant": from_participant,
            "signal": signal,
        },
    )
    notify_participant(to_participant)
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
        user_key(user_id),
        "call_diagnostic_signal",
        {"lane": lane, "signal": signal, "run_id": run_id},
    )
    notify_sse("chat", user_id)
    return envelope_id
