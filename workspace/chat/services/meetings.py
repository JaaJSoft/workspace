"""Creating a meeting and driving its lifecycle.

A meeting owns a dedicated conversation rather than attaching to an existing
one, so a guest admitted to the meeting has structurally nothing else to read.
"""

from django.db import IntegrityError, transaction
from django.utils import timezone

from .call_signaling import enqueue_event, notify_participant
from .meeting_occurrences import current_occurrence
from .participant_keys import guest_key


@transaction.atomic
def _create_meeting_once(event, created_by):
    from workspace.calendar.models import EventMember

    from ..models import Conversation, ConversationMember, Meeting

    existing = Meeting.objects.filter(event=event).first()
    if existing is not None:
        return existing

    conversation = Conversation.objects.create(
        kind=Conversation.Kind.GROUP,
        title=event.title,
        created_by=created_by,
    )
    member_ids = {event.owner_id, created_by.id}
    member_ids.update(
        EventMember.objects.filter(event=event)
        .exclude(status=EventMember.Status.DECLINED)
        .values_list("user_id", flat=True)
    )
    ConversationMember.objects.bulk_create(
        [
            ConversationMember(conversation=conversation, user_id=uid)
            for uid in sorted(member_ids)
        ]
    )
    return Meeting.objects.create(
        event=event, conversation=conversation, created_by=created_by
    )


def create_meeting(event, created_by):
    """Return the event's meeting, creating it and its conversation if needed.

    Membership of the meeting's conversation - which is what makes someone a
    host, see ``_meeting_for_host`` - is seeded once, at creation, from the
    event's owner, its creator and every invitee whose RSVP is not DECLINED
    (PENDING included). It is never re-synced afterwards: inviting someone
    to the event after the meeting exists grants them no host powers, and
    removing someone from the event does not revoke powers they already
    have. That is a deliberate limitation, not an oversight - reconciling
    live host membership raises its own questions (what happens to someone
    removed mid-meeting?) that are out of scope here.

    Two requests racing to create the same event's meeting both pass the
    ``existing is None`` check in ``_create_meeting_once``; the loser's atomic
    block rolls back cleanly (no orphan conversation) but trips ``Meeting.event``'s
    unique constraint on INSERT. Recovered the same way
    ``calls.start_or_join_call`` recovers the equivalent race on
    ``one_active_call_per_conversation``: retry a bounded number of times,
    identifying the race by the meeting now existing rather than masking an
    unrelated IntegrityError.
    """
    from ..models import Meeting

    max_attempts = 4
    for attempt in range(max_attempts):
        try:
            return _create_meeting_once(event, created_by)
        except IntegrityError:
            race_winner_exists = Meeting.objects.filter(event=event).exists()
            if not race_winner_exists or attempt == max_attempts - 1:
                raise
    # Unreachable: the final iteration either returns or re-raises above. Kept as
    # a defensive guard so the function never falls through to an implicit None.
    raise RuntimeError("create_meeting exhausted retries without returning")


def _notify_guest(guest, event_name, data=None):
    key = guest_key(guest.uuid)
    enqueue_event(key, event_name, data or {})
    notify_participant(key)


def admit_guest(guest, by_user):
    from ..models import MeetingGuest

    guest.state = MeetingGuest.State.ADMITTED
    guest.admitted_at = timezone.now()
    guest.admitted_by = by_user
    guest.save(update_fields=["state", "admitted_at", "admitted_by"])
    _notify_guest(guest, "meeting_admitted", {"meeting_id": str(guest.meeting_id)})
    return guest


def refuse_guest(guest):
    from ..models import MeetingGuest

    guest.state = MeetingGuest.State.REFUSED
    guest.save(update_fields=["state"])
    _notify_guest(guest, "meeting_refused")
    return guest


def remove_guest(guest):
    from ..models import MeetingGuest
    from .calls import close_guest_participation

    guest.state = MeetingGuest.State.REMOVED
    guest.removed_at = timezone.now()
    guest.save(update_fields=["state", "removed_at"])
    close_guest_participation(guest)
    _notify_guest(guest, "meeting_removed")
    return guest


@transaction.atomic
def set_locked(meeting, locked, now=None):
    """Lock or unlock the meeting, durably.

    Meeting.locked_occurrence_start is the value that survives with no active
    call - it is written unconditionally, so a host can pre-lock an empty
    room. It names the occurrence the lock was set during (from
    current_occurrence, never event.start), which is what stops a lock nobody
    ever released from following the series into next week. Locking outside
    any reachable occurrence therefore leaves nothing durable behind: there is
    no occurrence for the lock to belong to. When a call is already active,
    its session's live flag is written to match in the same call, so
    participants already in the room see the change too.

    Wrapped in one transaction so a failure on the second write cannot leave
    the durable value committed while the live one stays stale. Not
    guest-reachable (this is behind the host membership gate), so the
    get_active_call self-heal is fine here - it just has to settle before
    either write, see below.
    """
    from .calls import get_active_call

    locked = bool(locked)
    # Read the call BEFORE writing anything: get_active_call self-heals, and
    # on a phantom ACTIVE session (every heartbeat lapsed) that self-heal
    # ends the call through _end_call, which releases the durable lock. Run
    # after the write, it lands on top of the value just committed and leaves
    # the meeting unlocked while this call reports success.
    session = get_active_call(meeting.conversation_id)

    occurrence = current_occurrence(meeting, now=now) if locked else None
    meeting.locked_occurrence_start = occurrence[0] if occurrence is not None else None
    meeting.save(update_fields=["locked_occurrence_start"])

    if session is not None:
        session.locked = locked
        session.save(update_fields=["locked"])
    return True


@transaction.atomic
def end_meeting(meeting, now=None):
    """Close the occurrence that is reachable right now. False when none is."""
    from ..models import MeetingGuest
    from .calls import _end_call, get_active_call

    occurrence = current_occurrence(meeting, now=now)
    if occurrence is None:
        return False
    start, _end = occurrence
    meeting.closed_occurrence_start = start
    # The lock is scoped to the occurrence it was set during, and needs
    # clearing here as well as in calls._end_call: this branch also runs
    # with no active session (nothing to route through _end_call), which is
    # the ordinary case for a host ending a meeting nobody joined - a lock
    # set on an empty room must not survive that either. The two sites are
    # not redundant, both are needed for different starting states.
    meeting.locked_occurrence_start = None
    meeting.save(update_fields=["closed_occurrence_start", "locked_occurrence_start"])

    # A swept row can never become admittable again - resolve_guest denies
    # any occurrence_start matching closed_occurrence_start - so this also
    # reclaims the lobby slot it was holding rather than leaving it WAITING
    # forever (the slug is stable for the whole series, and nothing else
    # ever purges these rows).
    MeetingGuest.objects.filter(
        meeting=meeting, occurrence_start=start, state=MeetingGuest.State.WAITING
    ).update(state=MeetingGuest.State.REFUSED)

    session = get_active_call(meeting.conversation_id)
    if session is not None:
        _end_call(session)
    return True
