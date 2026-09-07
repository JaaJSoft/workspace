"""Creating a meeting and driving its lifecycle.

A meeting owns nothing but itself: hosts are derived from its event (see
meeting_hosts), guests hold tokens, and the chat is MeetingMessage.
"""

from django.db import IntegrityError, transaction
from django.utils import timezone

from .call_signaling import enqueue_event, notify_participant
from .meeting_hosts import host_ids
from .meeting_occurrences import current_occurrence
from .participant_keys import guest_key, user_key


@transaction.atomic
def _create_meeting_once(event, created_by):
    from ..models import Meeting

    existing = Meeting.objects.filter(event=event).first()
    if existing is not None:
        return existing
    return Meeting.objects.create(
        event=event, title=event.title[:200], created_by=created_by
    )


def create_ad_hoc_meeting(created_by, title):
    """A meeting with no event: its creator is its only host, and its only
    window is the call it carries (see current_occurrence)."""
    from ..models import Meeting

    return Meeting.objects.create(title=title[:200], created_by=created_by)


def create_meeting(event, created_by):
    """Return the event's meeting, creating it if needed.

    Two requests racing to create the same event's meeting both pass the
    ``existing is None`` check in ``_create_meeting_once``; the loser's atomic
    block rolls back cleanly (no orphan conversation) but trips ``Meeting.event``'s
    unique constraint on INSERT. Recovered the same way
    ``calls.start_or_join_call`` recovers the equivalent race on
    ``one_active_call_per_conversation``: retry a bounded number of times,
    identifying the race by the meeting now existing rather than masking an
    unrelated IntegrityError.

    A materialized exception never legitimately owns a Meeting - the join
    link is one row per series, stable across occurrences (see ``Meeting``
    in ``..models``) - so a caller passing an exception row is redirected to
    its series master here. An exception's owner is always copied from the
    master at creation time (see
    ``workspace.calendar.services.event_scope``), so a caller that already
    checked ownership on the exception row checked it on the row the
    meeting actually lands on too.
    """
    from ..models import Meeting

    if event.recurrence_parent_id:
        event = event.recurrence_parent

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


def notify_hosts(meeting, event_name, data):
    """Fan a meeting event out to every host through their own mailboxes."""
    for user_id in host_ids(meeting):
        key = user_key(user_id)
        enqueue_event(key, event_name, data)
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
    """Lock or unlock the meeting, durably. Returns the state committed.

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
    # ends the session under us. Reading it afterwards would either write the
    # live flag onto a row that is already ENDED, or - back when _end_call
    # also cleared the durable value - land that clear on top of the write
    # just made and report success over an unlocked meeting.
    session = (
        get_active_call(meeting.conversation_id) if meeting.conversation_id else None
    )

    occurrence = current_occurrence(meeting, now=now) if locked else None
    meeting.locked_occurrence_start = occurrence[0] if occurrence is not None else None
    meeting.save(update_fields=["locked_occurrence_start"])

    if session is not None:
        session.locked = locked
        session.save(update_fields=["locked"])
        return session.locked
    # No session to hold the live flag, so the durable value is the whole
    # answer - and it is False when there was no occurrence for the lock to
    # name. Returned rather than echoing the requested boolean, or the lock
    # endpoint would report a lock the very next summary read contradicts.
    return meeting.locked_occurrence_start is not None


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
    # The one place a lock is released other than the host unlocking: End is
    # the host asking, so the occurrence they locked is over for good. Ending
    # the call does not do this (see calls._end_call) - a room emptying out
    # is not a host reopening it.
    meeting.locked_occurrence_start = None
    meeting.save(update_fields=["closed_occurrence_start", "locked_occurrence_start"])

    # A swept row can never become admittable again - resolve_guest denies
    # any occurrence_start matching closed_occurrence_start - so this also
    # reclaims the lobby slot it was holding rather than leaving it WAITING
    # forever (the slug is stable for the whole series, and nothing else
    # ever purges these rows).
    #
    # Resolved before the update, so each swept guest can be told: without
    # the event their stream has nothing to yield and closes on a zero-byte
    # 200, which reads as a broken connection rather than a meeting that
    # ended. meeting_ended, not meeting_refused - nobody turned them away.
    swept = list(
        MeetingGuest.objects.filter(
            meeting=meeting, occurrence_start=start, state=MeetingGuest.State.WAITING
        ).only("uuid")
    )
    MeetingGuest.objects.filter(pk__in=[g.pk for g in swept]).update(
        state=MeetingGuest.State.REFUSED
    )
    for guest in swept:
        _notify_guest(guest, "meeting_ended")

    session = (
        get_active_call(meeting.conversation_id) if meeting.conversation_id else None
    )
    if session is not None:
        _end_call(session)
    return True
