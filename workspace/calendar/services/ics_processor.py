import logging

import icalendar
from django.db import transaction
from django.utils import timezone as django_tz

from workspace.calendar.models import Event, EventMember
from workspace.calendar.services.event_creation import create_event_from_payload
from workspace.calendar.services.ics_common import (
    extract_email,
    is_all_day,
    parse_dt_prop,
)
from workspace.calendar.services.timezones import normalize_all_day
from workspace.notifications.services.notifications import notify
from workspace.users.services.settings import get_user_timezone

logger = logging.getLogger(__name__)


def _parse_event_times(vevent, user):
    """Return (start, end, all_day, tzid) for a VEVENT, invariant-normalized."""
    owner_tz = get_user_timezone(user)
    start, tzid = parse_dt_prop(vevent.get("DTSTART"), owner_tz)
    end, _ = parse_dt_prop(vevent.get("DTEND"), owner_tz)
    all_day = is_all_day(vevent.get("DTSTART"))
    if all_day:
        start = normalize_all_day(start)
        end = normalize_all_day(end)
        tzid = ""
    return start, end, all_day, tzid


def process_calendar_email(mail_message):
    """Process a single mail message containing a text/calendar attachment."""
    attachment = mail_message.attachments.filter(content_type="text/calendar").first()
    if not attachment:
        return

    ics_data = attachment.content.read()
    cal = icalendar.Calendar.from_ical(ics_data)

    method = str(cal.get("METHOD", "REQUEST")).upper()

    for component in cal.walk():
        if component.name == "VEVENT":
            uid = str(component.get("UID"))
            if method == "CANCEL":
                _handle_cancel(component, uid, mail_message)
            else:
                _handle_request(component, uid, mail_message)


def process_calendar_emails(messages):
    """Process multiple mail messages, catching and logging exceptions for each."""
    for message in messages:
        try:
            process_calendar_email(message)
        except Exception:
            logger.exception("Failed to process calendar email %s", message.pk)


def _handle_request(vevent, uid, mail_message):
    """Handle a REQUEST method VEVENT (new invitation or update)."""
    account = mail_message.account
    user = account.owner
    sequence = int(vevent.get("SEQUENCE", 0))

    try:
        existing = Event.objects.get(ical_uid=uid, owner=user)
    except Event.DoesNotExist:
        _create_event(vevent, uid, sequence, mail_message)
        return

    if sequence <= existing.ical_sequence:
        return  # ignore duplicate or older sequence

    _update_event(existing, vevent, sequence, mail_message)


def _handle_cancel(vevent, uid, mail_message):
    """Handle a CANCEL method VEVENT."""
    account = mail_message.account
    user = account.owner

    try:
        event = Event.objects.get(ical_uid=uid, owner=user)
    except Event.DoesNotExist:
        return

    event.is_cancelled = True
    event.save(update_fields=["is_cancelled"])

    if _is_future_event(event):
        notify(
            recipient=user,
            origin="calendar",
            title=f"Cancelled: {event.title}",
            url=f"/calendar?event={event.pk}",
        )


@transaction.atomic
def _create_event(vevent, uid, sequence, mail_message):
    """Create a new Event from a VEVENT component."""
    user = mail_message.account.owner
    external_organizer = extract_email(vevent.get("ORGANIZER"))
    dtstart, dtend, all_day, tzid = _parse_event_times(vevent, user)

    event = create_event_from_payload(
        user=user,
        payload={
            "title": str(vevent.get("SUMMARY", "")),
            "description": str(vevent.get("DESCRIPTION", "")),
            "start": dtstart,
            "end": dtend,
            "all_day": all_day,
            "timezone": tzid,
            "location": str(vevent.get("LOCATION", "")),
        },
        source_message=mail_message,
        source=Event.Source.ICS,
        ical_uid=uid,
        ical_sequence=sequence,
        external_organizer=external_organizer,
    )

    EventMember.objects.create(
        event=event,
        user=user,
        status=EventMember.Status.PENDING,
    )

    if _is_future_event(event):
        notify(
            recipient=user,
            origin="calendar",
            title=f"Invitation: {event.title}",
            body=f"From {external_organizer}",
            url=f"/calendar?event={event.pk}",
        )

    return event


def _update_event(event, vevent, sequence, mail_message):
    """Update an existing Event from a newer VEVENT component."""
    start, end, all_day, tzid = _parse_event_times(vevent, event.owner)
    event.title = str(vevent.get("SUMMARY", ""))
    event.description = str(vevent.get("DESCRIPTION", ""))
    event.start = start
    event.end = end
    event.all_day = all_day
    event.timezone = tzid
    event.location = str(vevent.get("LOCATION", ""))
    event.ical_sequence = sequence
    event.source_message = mail_message

    event.save(
        update_fields=[
            "title",
            "description",
            "start",
            "end",
            "all_day",
            "timezone",
            "location",
            "ical_sequence",
            "source_message",
        ]
    )

    if _is_future_event(event):
        notify(
            recipient=event.owner,
            origin="calendar",
            title=f"Updated: {event.title}",
            body="The event has been updated",
            url=f"/calendar?event={event.pk}",
        )


def _is_future_event(event):
    """Return True if the event ends (or starts) in the future."""
    ref = event.end or event.start
    if ref is None:
        return True
    return ref > django_tz.now()
