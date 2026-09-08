"""The meeting's own chat: plain lines by hosts and admitted guests.

Delivery is the participant mailbox both audiences already drain (hosts
through the global stream, guests through their token stream), so there is
one path to keep correct. A reader that reconnects reloads the list; the
mailbox is not a history.
"""

from django.db import transaction

from .call_signaling import enqueue_event, notify_participant
from .identities import identity_payload
from .meeting_guests import admitted_guest_keys
from .meeting_hosts import host_ids
from .meeting_occurrences import current_occurrence
from .participant_keys import guest_key, user_key
from .rendering import render_message_body

MEETING_MESSAGE_MAX_LENGTH = 4000
_SELECT_RELATED = ("author", "guest", "guest__user")


def serialize_message(message):
    return {
        "uuid": str(message.uuid),
        "body": message.body,
        "body_html": message.body_html,
        "created_at": message.created_at.isoformat(),
        "occurrence_start": (
            message.occurrence_start.isoformat() if message.occurrence_start else None
        ),
        "author": identity_payload(message.author, message.guest),
    }


def recipient_keys(meeting, occurrence_start):
    keys = [user_key(uid) for uid in host_ids(meeting)]
    if occurrence_start is not None:
        keys += admitted_guest_keys(meeting, occurrence_start)
    return keys


def _fan_out(meeting, occurrence_start, event_name, data, exclude_key=None):
    for key in recipient_keys(meeting, occurrence_start):
        if key == exclude_key:
            continue
        enqueue_event(key, event_name, data)
        notify_participant(key)


def post_message(meeting, body, *, author=None, guest=None, now=None):
    from ..models import MeetingMessage

    occurrence = current_occurrence(meeting, now=now)
    occurrence_start = occurrence[0] if occurrence is not None else None
    with transaction.atomic():
        message = MeetingMessage.objects.create(
            meeting=meeting,
            occurrence_start=occurrence_start,
            author=author,
            guest=guest,
            body=body,
            body_html=render_message_body(body, allow_everyone=False),
        )
    message = MeetingMessage.objects.select_related(*_SELECT_RELATED).get(pk=message.pk)
    own_key = user_key(author.id) if author is not None else guest_key(guest.uuid)
    _fan_out(
        meeting,
        occurrence_start,
        "meeting_message",
        {"meeting_id": str(meeting.uuid), "message": serialize_message(message)},
        exclude_key=own_key,
    )
    return message


def delete_message(message):
    meeting = message.meeting
    occurrence_start = message.occurrence_start
    message_id = str(message.uuid)
    message.delete()
    _fan_out(
        meeting,
        occurrence_start,
        "meeting_message_deleted",
        {"meeting_id": str(meeting.uuid), "message_id": message_id},
    )


def _page(qs, before, limit):
    if before is not None:
        # values_list bypasses select_related entirely, avoiding the
        # deferred-field/select_related clash .only() would hit on a
        # queryset that already carries select_related(*_SELECT_RELATED).
        cursor_created_at = (
            qs.filter(uuid=before).values_list("created_at", flat=True).first()
        )
        if cursor_created_at is not None:
            qs = qs.filter(created_at__lt=cursor_created_at)
    rows = list(qs.order_by("-created_at")[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    rows.reverse()
    return rows, has_more


def messages_for_host(meeting, *, before=None, limit=50):
    from ..models import MeetingMessage

    qs = MeetingMessage.objects.select_related(*_SELECT_RELATED).filter(meeting=meeting)
    return _page(qs, before, limit)


def messages_for_guest(guest, *, before=None, limit=50):
    """Only the guest's own occurrence: the floor is one equality."""
    from ..models import MeetingMessage

    qs = MeetingMessage.objects.select_related(*_SELECT_RELATED).filter(
        meeting_id=guest.meeting_id, occurrence_start=guest.occurrence_start
    )
    return _page(qs, before, limit)
