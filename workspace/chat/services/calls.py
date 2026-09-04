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

from .call_signaling import enqueue_event, notify_participant
from .identities import display_name_for_identity
from .participant_keys import guest_key, user_key

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


def touch_presence(session_id, participant_key, media_state):
    """Refresh *participant_key*'s heartbeat. Returns True if media_state changed."""
    key = _presence_key(session_id)
    data = cache.get(key) or {}
    prev = data.get(participant_key)
    changed = prev != media_state
    data[participant_key] = media_state
    cache.set(key, data, presence_ttl())
    return changed


def get_presence(session_id):
    """Return `{participant_key: media_state}` for participants with a fresh heartbeat."""
    return cache.get(_presence_key(session_id)) or {}


def drop_presence(session_id, participant_key):
    key = _presence_key(session_id)
    data = cache.get(key)
    if data and participant_key in data:
        del data[participant_key]
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


def active_call_session(conversation_id):
    """Plain (non-locking, non-self-healing) read of *conversation_id*'s
    active call session, or None.

    Sibling of ``active_call_session_for_guest``, for the host-reachable but
    still-anonymous-adjacent read in ``MeetingSummaryView``: that view is
    unauthenticated (``AllowAny``), so it must never reach for
    ``get_active_call`` - see that function's docstring and
    ``is_call_locked`` for why a self-healing read must not run off a plain
    GET.
    """
    from ..models import CallSession

    return CallSession.objects.filter(
        conversation_id=conversation_id, state=CallSession.State.ACTIVE
    ).first()


def is_call_locked(conversation_id, occurrence_start=None):
    """Whether *conversation_id* has a locked call, no self-heal.

    Unlike ``get_active_call``, this never takes the cleanup write-lock or
    ends a stale call - it is for the public meeting endpoints, which are
    reachable by an anonymous caller holding only a slug and must not be
    able to drive a DB write and an SSE broadcast off a plain GET/POST.

    Prefers the session's flag (the live value) while a call is active, and
    falls back to the meeting's durable lock when there is no session yet -
    a host can pre-lock an empty room, see set_locked. That fallback answers
    only for the occurrence the lock was set during, which is why
    *occurrence_start* is the caller's: every caller has already resolved
    the occurrence it is asking about, and re-deriving it here would both
    repeat that work and let the two answers disagree. None means "no
    occurrence is reachable right now", which no durable lock can match.
    """
    from ..models import CallSession, Meeting

    session = (
        CallSession.objects.filter(
            conversation_id=conversation_id, state=CallSession.State.ACTIVE
        )
        .only("locked")
        .first()
    )
    if session is not None:
        return session.locked
    if occurrence_start is None:
        return False
    return Meeting.objects.filter(
        conversation_id=conversation_id, locked_occurrence_start=occurrence_start
    ).exists()


def _durable_lock_holds(meeting):
    """Whether *meeting*'s durable lock names the occurrence reachable now.

    Only for the paths that hold a Meeting instance and no occurrence of
    their own (the session seed in ``_start_or_join_once``); everywhere else
    the caller already resolved the occurrence and passes it to
    ``is_call_locked``.
    """
    from .meeting_occurrences import current_occurrence

    if meeting is None or meeting.locked_occurrence_start is None:
        return False
    occurrence = current_occurrence(meeting)
    return occurrence is not None and meeting.locked_occurrence_start == occurrence[0]


def active_call_session_for_guest(guest):
    """Plain (non-locking, non-self-healing) read of *guest*'s meeting's active
    call session, or None.

    For the guest-reachable state/heartbeat endpoints: like ``is_call_locked``,
    this must never be ``get_active_call``, whose self-heal can end a stale
    call and fan out a broadcast off what looks like a harmless read from an
    anonymous caller.
    """
    from ..models import CallSession

    return CallSession.objects.filter(
        conversation_id=guest.meeting.conversation_id, state=CallSession.State.ACTIVE
    ).first()


def _has_stale_participants(session):
    """Whether any active participant lacks a fresh heartbeat (no DB lock)."""
    from ..models import CallParticipant

    fresh = set(get_presence(session.uuid))
    active = CallParticipant.objects.filter(session=session, left_at__isnull=True).only(
        "user_id", "guest_id"
    )
    return any(p.participant_key not in fresh for p in active)


def close_guest_participation(guest):
    """Close any active call participant row for *guest* and drop their heartbeat.

    Called when a host removes a guest from the meeting. Nothing else sweeps
    this row: _active_guest_keys already excludes a removed guest from the
    fan-out by state, but without this their CallParticipant row survives,
    still holding a capacity slot and a stale entry in
    list_active_participants until the heartbeat lapses on its own.
    """
    from ..models import CallParticipant, CallSession

    key = guest_key(guest.uuid)
    participants = list(
        CallParticipant.objects.select_related("session").filter(
            guest=guest, left_at__isnull=True, session__state=CallSession.State.ACTIVE
        )
    )
    for participant in participants:
        drop_presence(participant.session_id, key)
    CallParticipant.objects.filter(pk__in=[p.pk for p in participants]).update(
        left_at=timezone.now()
    )

    # Same fan-out every other leave path performs (leave_call,
    # leave_call_as_guest, cleanup_stale_participants); without it the removed
    # guest's tile and RTCPeerConnection stay up for everyone else. After the
    # rows are closed, so the recipient lookup no longer includes them.
    for participant in participants:
        _broadcast(
            participant.session.conversation_id,
            "call_participant_left",
            {"session_id": str(participant.session_id), "participant_key": key},
        )


def list_active_participants(session):
    from ..models import CallParticipant

    return list(
        CallParticipant.objects.filter(session=session, left_at__isnull=True)
        .select_related("user", "guest")
        .order_by("joined_at")
    )


def active_member_ids(conversation_id):
    from ..models import ConversationMember

    return list(
        ConversationMember.objects.filter(
            conversation_id=conversation_id, left_at__isnull=True
        ).values_list("user_id", flat=True)
    )


def _active_guest_keys(conversation_id):
    """Participant keys for admitted guests in the conversation's active call.

    A plain read on purpose: get_active_call self-heals, and _broadcast is
    called from inside that self-heal (via cleanup_stale_participants), so
    reaching for it here would recurse.

    guest__state=ADMITTED matters: a host removing a guest flips MeetingGuest.state
    but leaves the CallParticipant row alone (nothing sweeps it), so without this
    filter a removed guest would keep receiving every call event until their
    heartbeat lapses.
    """
    from ..models import CallParticipant, CallSession, MeetingGuest

    return [
        guest_key(guest_uuid)
        for guest_uuid in CallParticipant.objects.filter(
            session__conversation_id=conversation_id,
            session__state=CallSession.State.ACTIVE,
            left_at__isnull=True,
            guest__isnull=False,
            guest__state=MeetingGuest.State.ADMITTED,
        ).values_list("guest_id", flat=True)
    ]


def _active_recipient_keys(conversation_id):
    """Every active member key plus every admitted-guest-in-the-call key."""
    return [
        user_key(uid) for uid in active_member_ids(conversation_id)
    ] + _active_guest_keys(conversation_id)


def _broadcast(conversation_id, event, data, exclude_key=None, recipients=None):
    """Fan a call event out to every active member and guest, then wake them.

    *recipients*, when given, is used verbatim instead of a fresh lookup. This
    matters for call_ended: _active_guest_keys only matches an ACTIVE session,
    so a caller that flips the session to ENDED before broadcasting must
    resolve who was in the call first and pass that list through, or every
    guest recipient silently drops out of their own end-of-call notice.
    """
    keys = _active_recipient_keys(conversation_id) if recipients is None else recipients
    for key in keys:
        if exclude_key is not None and key == exclude_key:
            continue
        enqueue_event(key, event, data)
        notify_participant(key)


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
    from ..models import CallParticipant, CallSession, Meeting, Message

    session = _active_session_for_update(conversation_id)
    created_session = False
    if session is None:
        # Seed from the meeting's durable lock, if this conversation belongs
        # to one: a host locking the room before anyone has joined still
        # finds it locked on the session created here. Plain (non-meeting)
        # conversations have no Meeting row, so this is a no-op for them.
        meeting = (
            Meeting.objects.select_related("event")
            .filter(conversation_id=conversation_id)
            .first()
        )
        session = CallSession.objects.create(
            conversation_id=conversation_id,
            started_by=user,
            locked=_durable_lock_holds(meeting),
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

    key = user_key(user.id)
    touch_presence(session.uuid, key, DEFAULT_MEDIA_STATE)

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
                "participant_key": key,
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
def join_call_as_guest(guest):
    """Add *guest* as a participant in their meeting's already-active call.

    Unlike ``start_or_join_call``, this never creates a session:
    ``CallSession.started_by`` is a user, and a guest has no user row, so
    there is nothing for a fresh session to be attributed to - a guest can
    only join a call a host has already started. Returns None when there is
    no active call (the caller maps that to 404); raises CallFull when the
    cap is reached.

    Uses ``_active_session_for_update``, not ``get_active_call``: this is
    guest-reachable with only a token, and get_active_call's self-heal must
    never run off that.
    """
    from ..models import CallParticipant

    session = _active_session_for_update(guest.meeting.conversation_id)
    if session is None:
        return None

    active_qs = CallParticipant.objects.filter(session=session, left_at__isnull=True)
    if not active_qs.filter(guest=guest).exists():
        if active_qs.count() >= max_participants():
            raise CallFull()

    participant, _ = CallParticipant.objects.get_or_create(
        session=session, guest=guest, defaults={"left_at": None}
    )
    if participant.left_at is not None:
        participant.left_at = None
        participant.save(update_fields=["left_at"])

    key = guest_key(guest.uuid)
    touch_presence(session.uuid, key, DEFAULT_MEDIA_STATE)

    _broadcast(
        guest.meeting.conversation_id,
        "call_participant_joined",
        {
            "session_id": str(session.uuid),
            "participant_key": key,
            "user_id": None,
            "display_name": guest.display_name,
            "media_state": DEFAULT_MEDIA_STATE,
        },
    )
    return session


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

    key = user_key(user.id)
    CallParticipant.objects.filter(
        session=session, user=user, left_at__isnull=True
    ).update(left_at=timezone.now())
    drop_presence(session.uuid, key)

    if CallParticipant.objects.filter(session=session, left_at__isnull=True).exists():
        _broadcast(
            conversation_id,
            "call_participant_left",
            {"session_id": str(session.uuid), "participant_key": key},
        )
        return session

    return _end_call(session)


@transaction.atomic
def leave_call_as_guest(guest):
    """Guest counterpart of ``leave_call``. A no-op when there is no active call."""
    from ..models import CallParticipant, CallSession

    session = (
        CallSession.objects.select_for_update()
        .filter(
            conversation_id=guest.meeting.conversation_id,
            state=CallSession.State.ACTIVE,
        )
        .first()
    )
    if session is None:
        return None

    key = guest_key(guest.uuid)
    CallParticipant.objects.filter(
        session=session, guest=guest, left_at__isnull=True
    ).update(left_at=timezone.now())
    drop_presence(session.uuid, key)

    if CallParticipant.objects.filter(session=session, left_at__isnull=True).exists():
        _broadcast(
            guest.meeting.conversation_id,
            "call_participant_left",
            {"session_id": str(session.uuid), "participant_key": key},
        )
        return session

    return _end_call(session)


def _end_call(session, recipients=None):
    """Mark a session ended, finalize its system message, broadcast call_ended.

    Deliberately leaves Meeting.locked_occurrence_start alone: a call
    emptying out is not the host unlocking the room, and the durable value
    already stops answering once its occurrence is no longer the current one.
    Clearing it here would reopen, mid-occurrence, a room the host shut.
    end_meeting clears it, because End is the host asking.

    *recipients* is for a caller that has already closed participations
    before getting here - the stale sweep does, and _active_guest_keys only
    matches a still-joined guest, so resolving them below would find nobody.
    """
    from ..models import CallSession

    # Resolve who is in the call before flipping state to ENDED:
    # _active_guest_keys only matches an ACTIVE session, so computing this
    # after the save below would silently drop every guest from call_ended.
    if recipients is None:
        recipients = _active_recipient_keys(session.conversation_id)

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
        recipients=recipients,
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

    fresh = set(get_presence(session.uuid))
    # Before anyone is marked left: _active_guest_keys matches only a guest
    # still joined to an ACTIVE session, so resolving this after the update
    # below would drop every guest from the call_ended fan-out.
    recipients = _active_recipient_keys(session.conversation_id)
    # Bounded by CHAT_CALL_MAX_PARTICIPANTS, and the session row lock above
    # serializes this against concurrent joins - safe to materialize.
    active = list(CallParticipant.objects.filter(session=session, left_at__isnull=True))
    stale = [p for p in active if p.participant_key not in fresh]
    if stale:
        CallParticipant.objects.filter(pk__in=[p.pk for p in stale]).update(
            left_at=timezone.now()
        )
        for p in stale:
            _broadcast(
                session.conversation_id,
                "call_participant_left",
                {
                    "session_id": str(session.uuid),
                    "participant_key": p.participant_key,
                },
            )

    if not CallParticipant.objects.filter(
        session=session, left_at__isnull=True
    ).exists():
        # State is guaranteed ACTIVE here (locked + filtered above).
        _end_call(session, recipients=recipients)
        return True
    return False


def end_stale_calls():
    """Celery-driven sweep: end every active call with no live participants."""
    from ..models import CallSession

    ended = 0
    for session in CallSession.objects.filter(state=CallSession.State.ACTIVE).only(
        "pk"
    ):
        if cleanup_stale_participants(session):
            ended += 1
    return ended


def serialize_call_state(session):
    presence = get_presence(session.uuid)
    participants = []
    for p in list_active_participants(session):
        key = p.participant_key
        participants.append(
            {
                "participant_key": key,
                # Kept beside the key so the avatar element can resolve a face.
                # None for a meeting guest, which has no user row.
                "user_id": p.user_id,
                "display_name": display_name_for_identity(p.user, p.guest),
                "media_state": presence.get(key, dict(DEFAULT_MEDIA_STATE)),
            }
        )
    return {
        "active": session.state == session.State.ACTIVE,
        "session_id": str(session.uuid),
        "conversation_id": str(session.conversation_id),
        "started_by": session.started_by_id,
        "started_at": session.started_at.isoformat(),
        "media_kind": session.media_kind,
        "locked": session.locked,
        "max_participants": max_participants(),
        "participants": participants,
    }
