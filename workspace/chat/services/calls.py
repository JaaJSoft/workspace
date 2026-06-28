"""Call lifecycle, presence and cleanup for chat voice rooms.

Durable state (CallSession, CallParticipant, the system message) is in the DB.
Live presence is a cache heartbeat (auto-expiring) so a crashed/closed tab is
reaped without a clean "leave". Lifecycle mutations fan out cache events via
``call_signaling`` so the SSE poll delivers them in near real time.
"""

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from workspace.core.sse_registry import notify_sse

from .call_signaling import enqueue_event

DEFAULT_MEDIA_STATE = {"audio": True}


def format_duration(seconds):
    """Human label for a call duration. No em-dash; uses 'min' / 'h'."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours} h {minutes:02d}"


def presence_ttl():
    return int(getattr(settings, "CHAT_CALL_PRESENCE_TTL", 12))


def _presence_key(session_id):
    return f"chat:call_presence:{session_id}"


def touch_presence(session_id, user_id, media_state):
    """Refresh *user_id*'s heartbeat. Returns True if media_state changed."""
    key = _presence_key(session_id)
    data = cache.get(key) or {}
    prev = data.get(str(user_id))
    changed = prev != media_state
    data[str(user_id)] = media_state
    cache.set(key, data, presence_ttl())
    return changed


def get_presence(session_id):
    """Return `{user_id_str: media_state}` for users with a fresh heartbeat."""
    return cache.get(_presence_key(session_id)) or {}


def drop_presence(session_id, user_id):
    key = _presence_key(session_id)
    data = cache.get(key)
    if data and str(user_id) in data:
        del data[str(user_id)]
        cache.set(key, data, presence_ttl())


class CallFull(Exception):
    """Raised when a join would exceed CHAT_CALL_MAX_PARTICIPANTS."""


def max_participants():
    return int(getattr(settings, "CHAT_CALL_MAX_PARTICIPANTS", 6))


def get_active_call(conversation_id):
    from ..models import CallSession

    session = (
        CallSession.objects.filter(
            conversation_id=conversation_id, state=CallSession.State.ACTIVE
        )
        .select_related("system_message", "started_by")
        .first()
    )
    if session is None:
        return None
    # Self-heal on read: a call is only really "in progress" if at least one
    # participant still has a live heartbeat. Reconcile the durable ACTIVE row
    # against ephemeral presence so a phantom call (tab crash, lost network,
    # server or cache restart, or the Celery beat sweep not running) is ended on
    # the next read instead of advertising a dead call forever. The cheap stale
    # check avoids taking the cleanup write-lock on every healthy read.
    if _has_stale_participants(session) and cleanup_stale_participants(session):
        return None
    return session


def _has_stale_participants(session):
    """Whether any active participant lacks a fresh heartbeat (no DB lock)."""
    from ..models import CallParticipant

    fresh = set(get_presence(session.uuid).keys())
    active_ids = CallParticipant.objects.filter(
        session=session, left_at__isnull=True
    ).values_list("user_id", flat=True)
    return any(str(uid) not in fresh for uid in active_ids)


def list_active_participants(session):
    from ..models import CallParticipant

    return list(
        CallParticipant.objects.filter(session=session, left_at__isnull=True)
        .select_related("user")
        .order_by("joined_at")
    )


def _active_member_ids(conversation_id):
    from ..models import ConversationMember

    return list(
        ConversationMember.objects.filter(
            conversation_id=conversation_id, left_at__isnull=True
        ).values_list("user_id", flat=True)
    )


def _broadcast(conversation_id, event, data, exclude_user_id=None):
    """Fan a call event out to every active conversation member, then wake them."""
    for uid in _active_member_ids(conversation_id):
        if exclude_user_id is not None and uid == exclude_user_id:
            continue
        enqueue_event(uid, event, data)
        notify_sse("chat", uid)


def _render_system_call_body(state, duration_label=None):
    """Plain-text fallback body. The visible bubble is rendered by the template
    from tool_data; body keeps the message readable in previews/search."""
    if state == "ended":
        return f"Call ended - {duration_label}" if duration_label else "Call ended"
    return "Call started"


def _active_session_for_update(conversation_id):
    """Locked read of the conversation's active call session (or None).

    Isolated as a single mockable seam: the first-join race tests simulate the
    loser's stale "no active call" read by patching this one function. (The
    retry re-reads because start_or_join_call re-invokes the whole atomic body,
    not because this lookup is a separate function.)
    """
    from ..models import CallSession

    return (
        CallSession.objects.select_for_update()
        .filter(conversation_id=conversation_id, state=CallSession.State.ACTIVE)
        .first()
    )


@transaction.atomic
def _start_or_join_once(user, conversation_id):
    from ..models import CallParticipant, CallSession, Message

    session = _active_session_for_update(conversation_id)
    created_session = False
    if session is None:
        session = CallSession.objects.create(
            conversation_id=conversation_id, started_by=user
        )
        msg = Message.objects.create(
            conversation_id=conversation_id,
            author=user,
            kind=Message.Kind.SYSTEM,
            body=_render_system_call_body("active"),
            tool_data={
                "type": "call",
                "session_id": str(session.uuid),
                "media_kind": session.media_kind,
                "state": "active",
            },
        )
        session.system_message = msg
        session.save(update_fields=["system_message"])
        created_session = True

    # Capacity check counts currently-active participants (excluding a rejoin).
    active_qs = CallParticipant.objects.filter(session=session, left_at__isnull=True)
    if not active_qs.filter(user=user).exists():
        if active_qs.count() >= max_participants():
            raise CallFull()

    participant, _ = CallParticipant.objects.get_or_create(
        session=session, user=user, defaults={"left_at": None}
    )
    if participant.left_at is not None:
        participant.left_at = None
        participant.save(update_fields=["left_at"])

    touch_presence(session.uuid, user.id, DEFAULT_MEDIA_STATE)

    display_name = user.get_full_name() or user.username
    if created_session:
        _broadcast(
            conversation_id,
            "call_started",
            {
                "session_id": str(session.uuid),
                "conversation_id": str(conversation_id),
                "started_by": user.id,
                "media_kind": session.media_kind,
            },
        )
    else:
        _broadcast(
            conversation_id,
            "call_participant_joined",
            {
                "session_id": str(session.uuid),
                "user_id": user.id,
                "display_name": display_name,
                "media_state": DEFAULT_MEDIA_STATE,
            },
        )

    return session, participant, created_session


def start_or_join_call(user, conversation_id):
    from ..models import CallSession

    # Only the first-join race is recoverable: a competing request committed the
    # active session between our "no active call" read and our INSERT, tripping
    # the one_active_call_per_conversation partial unique constraint. That race is
    # identifiable by an active session now existing (our atomic block rolled
    # back). Any other IntegrityError is a real failure and must propagate rather
    # than be masked by a blind retry.
    #
    # A single retry only closes the two-party race; retry a bounded number of
    # times so a rarer compound race (the winner ends its call and a third member
    # starts a fresh one in the gap, re-tripping the constraint) also recovers
    # instead of surfacing as a 500. The bound guarantees termination.
    max_attempts = 4
    for attempt in range(max_attempts):
        try:
            return _start_or_join_once(user, conversation_id)
        except IntegrityError:
            race_winner_exists = CallSession.objects.filter(
                conversation_id=conversation_id, state=CallSession.State.ACTIVE
            ).exists()
            if not race_winner_exists or attempt == max_attempts - 1:
                raise
    # Unreachable: the final iteration either returns or re-raises above. Kept as
    # a defensive guard so the function never falls through to an implicit None.
    raise RuntimeError("start_or_join_call exhausted retries without returning")


@transaction.atomic
def leave_call(user, conversation_id):
    from ..models import CallParticipant, CallSession

    session = (
        CallSession.objects.select_for_update()
        .filter(conversation_id=conversation_id, state=CallSession.State.ACTIVE)
        .first()
    )
    if session is None:
        return None

    CallParticipant.objects.filter(
        session=session, user=user, left_at__isnull=True
    ).update(left_at=timezone.now())
    drop_presence(session.uuid, user.id)

    if CallParticipant.objects.filter(session=session, left_at__isnull=True).exists():
        _broadcast(
            conversation_id,
            "call_participant_left",
            {"session_id": str(session.uuid), "user_id": user.id},
        )
        return session

    return _end_call(session)


def _end_call(session):
    """Mark a session ended, finalize its system message, broadcast call_ended."""
    from ..models import CallSession

    session.state = CallSession.State.ENDED
    session.ended_at = timezone.now()
    session.save(update_fields=["state", "ended_at"])

    duration = session.duration_seconds or 0
    label = format_duration(duration)
    msg = session.system_message
    if msg is not None:
        data = dict(msg.tool_data or {})
        data["state"] = "ended"
        data["duration_seconds"] = duration
        data["duration_label"] = label
        msg.tool_data = data
        msg.body = _render_system_call_body("ended", label)
        msg.edited_at = timezone.now()
        msg.save(update_fields=["tool_data", "body", "edited_at"])

    _broadcast(
        session.conversation_id,
        "call_ended",
        {
            "session_id": str(session.uuid),
            "duration": duration,
            "duration_label": label,
        },
    )
    return session


@transaction.atomic
def cleanup_stale_participants(session):
    """Reap participants whose heartbeat expired; end the call if none remain."""
    from ..models import CallParticipant, CallSession

    # Lock the session row and re-read its state: concurrent sweeps (or a racing
    # leave_call) must not both run the end-call path on the same session, which
    # would fire call_ended twice and finalize the system message twice.
    session = (
        CallSession.objects.select_for_update()
        .filter(pk=session.pk, state=CallSession.State.ACTIVE)
        .first()
    )
    if session is None:
        return False

    fresh = set(get_presence(session.uuid).keys())
    stale = CallParticipant.objects.filter(
        session=session, left_at__isnull=True
    ).exclude(user_id__in=[int(uid) for uid in fresh])
    left_ids = list(stale.values_list("user_id", flat=True))
    if left_ids:
        stale.update(left_at=timezone.now())
        for uid in left_ids:
            _broadcast(
                session.conversation_id,
                "call_participant_left",
                {"session_id": str(session.uuid), "user_id": uid},
            )

    if not CallParticipant.objects.filter(
        session=session, left_at__isnull=True
    ).exists():
        # State is guaranteed ACTIVE here (locked + filtered above).
        _end_call(session)
        return True
    return False


def end_stale_calls():
    """Celery-driven sweep: end every active call with no live participants."""
    from ..models import CallSession

    ended = 0
    for session in CallSession.objects.filter(
        state=CallSession.State.ACTIVE
    ).select_related("system_message"):
        if cleanup_stale_participants(session):
            ended += 1
    return ended


def serialize_call_state(session):
    presence = get_presence(session.uuid)
    participants = []
    for p in list_active_participants(session):
        participants.append(
            {
                "user_id": p.user_id,
                "display_name": p.user.get_full_name() or p.user.username,
                "media_state": presence.get(str(p.user_id), dict(DEFAULT_MEDIA_STATE)),
            }
        )
    return {
        "active": session.state == session.State.ACTIVE,
        "session_id": str(session.uuid),
        "conversation_id": str(session.conversation_id),
        "started_by": session.started_by_id,
        "started_at": session.started_at.isoformat(),
        "media_kind": session.media_kind,
        "participants": participants,
    }
