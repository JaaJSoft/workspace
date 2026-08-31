"""Creating a meeting and driving its lifecycle.

A meeting owns a dedicated conversation rather than attaching to an existing
one, so a guest admitted to the meeting has structurally nothing else to read.
"""

from django.db import transaction
from django.utils import timezone

from .call_signaling import enqueue_event, notify_participant
from .meeting_occurrences import current_occurrence
from .participant_keys import guest_key


@transaction.atomic
def create_meeting(event, created_by):
    """Return the event's meeting, creating it and its conversation if needed."""
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
        EventMember.objects.filter(event=event).values_list("user_id", flat=True)
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

    guest.state = MeetingGuest.State.REMOVED
    guest.removed_at = timezone.now()
    guest.save(update_fields=["state", "removed_at"])
    _notify_guest(guest, "meeting_removed")
    return guest


def set_locked(meeting, locked):
    """Lock or unlock the meeting's active call session. False when none."""
    from .calls import get_active_call

    session = get_active_call(meeting.conversation_id)
    if session is None:
        return False
    session.locked = bool(locked)
    session.save(update_fields=["locked"])
    return True


def end_meeting(meeting, now=None):
    """Close the occurrence that is reachable right now. False when none is."""
    from .calls import _end_call, get_active_call

    occurrence = current_occurrence(meeting, now=now)
    if occurrence is None:
        return False
    start, _end = occurrence
    meeting.closed_occurrence_start = start
    meeting.save(update_fields=["closed_occurrence_start"])

    session = get_active_call(meeting.conversation_id)
    if session is not None:
        _end_call(session)
    return True
