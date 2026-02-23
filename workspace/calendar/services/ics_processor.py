import logging
from datetime import datetime, timezone

import icalendar

from workspace.calendar.models import Calendar, Event, EventMember
from workspace.notifications.services import notify

logger = logging.getLogger(__name__)


def process_calendar_email(mail_message):
    """Process a single mail message containing a text/calendar attachment."""
    attachment = mail_message.attachments.filter(content_type='text/calendar').first()
    if not attachment:
        return

    ics_data = attachment.content.read()
    cal = icalendar.Calendar.from_ical(ics_data)

    method = str(cal.get('METHOD', 'REQUEST')).upper()

    for component in cal.walk():
        if component.name == 'VEVENT':
            uid = str(component.get('UID'))
            if method == 'CANCEL':
                _handle_cancel(component, uid, mail_message)
            else:
                _handle_request(component, uid, mail_message)


def process_calendar_emails(messages):
    """Process multiple mail messages, catching and logging exceptions for each."""
    for message in messages:
        try:
            process_calendar_email(message)
        except Exception:
            logger.exception('Failed to process calendar email %s', message.pk)


def _handle_request(vevent, uid, mail_message):
    """Handle a REQUEST method VEVENT (new invitation or update)."""
    account = mail_message.account
    user = account.owner
    sequence = int(vevent.get('SEQUENCE', 0))

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
    event.save(update_fields=['is_cancelled'])

    notify(
        recipient=user,
        origin='calendar',
        title=f'Cancelled: {event.title}',
    )


def _create_event(vevent, uid, sequence, mail_message):
    """Create a new Event from a VEVENT component."""
    account = mail_message.account
    user = account.owner
    calendar = _get_or_create_invitation_calendar(account)

    organizer_email = _extract_email(vevent.get('ORGANIZER'))
    dtstart = _to_datetime(vevent.get('DTSTART'))
    dtend = _to_datetime(vevent.get('DTEND'))
    all_day = _is_all_day(vevent.get('DTSTART'))

    title = str(vevent.get('SUMMARY', ''))
    description = str(vevent.get('DESCRIPTION', ''))
    location = str(vevent.get('LOCATION', ''))

    event = Event.objects.create(
        calendar=calendar,
        title=title,
        description=description,
        start=dtstart,
        end=dtend,
        all_day=all_day,
        location=location,
        owner=user,
        ical_uid=uid,
        ical_sequence=sequence,
        organizer_email=organizer_email,
        source_message=mail_message,
    )

    EventMember.objects.create(
        event=event,
        user=user,
        status=EventMember.Status.PENDING,
    )

    notify(
        recipient=user,
        origin='calendar',
        title=f'Invitation: {title}',
        body=f'From {organizer_email}',
    )

    return event


def _update_event(event, vevent, sequence, mail_message):
    """Update an existing Event from a newer VEVENT component."""
    event.title = str(vevent.get('SUMMARY', ''))
    event.description = str(vevent.get('DESCRIPTION', ''))
    event.start = _to_datetime(vevent.get('DTSTART'))
    event.end = _to_datetime(vevent.get('DTEND'))
    event.all_day = _is_all_day(vevent.get('DTSTART'))
    event.location = str(vevent.get('LOCATION', ''))
    event.ical_sequence = sequence
    event.source_message = mail_message

    event.save(update_fields=[
        'title', 'description', 'start', 'end', 'all_day',
        'location', 'ical_sequence', 'source_message',
    ])

    notify(
        recipient=event.owner,
        origin='calendar',
        title=f'Updated: {event.title}',
        body='The event has been updated',
    )


def _get_or_create_invitation_calendar(account):
    """Get or create the invitation calendar for a mail account."""
    calendar = Calendar.objects.filter(mail_account=account).first()
    if calendar:
        expected_name = account.display_name or account.email
        if calendar.name != expected_name:
            calendar.name = expected_name
            calendar.save(update_fields=['name', 'updated_at'])
        return calendar

    return Calendar.objects.create(
        name=account.display_name or account.email,
        color='secondary',
        owner=account.owner,
        mail_account=account,
    )


def _extract_email(organizer_prop):
    """Extract email address from an ORGANIZER or ATTENDEE property."""
    if not organizer_prop:
        return ''
    value = str(organizer_prop)
    if value.lower().startswith('mailto:'):
        return value[7:]
    return value


def _to_datetime(dt_prop):
    """Convert an icalendar date/datetime property to a timezone-aware datetime."""
    if dt_prop is None:
        return None
    dt = dt_prop.dt
    if hasattr(dt, 'hour'):
        # It's a datetime
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    else:
        # It's a date (all-day event) - convert to datetime at midnight UTC
        return datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)


def _is_all_day(dt_prop):
    """Return True if the DTSTART property represents an all-day event (date, not datetime)."""
    if dt_prop is None:
        return False
    return not hasattr(dt_prop.dt, 'hour')
