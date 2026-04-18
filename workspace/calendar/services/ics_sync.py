"""Fetch and sync external ICS calendar feeds."""
import logging
from datetime import datetime, timedelta, timezone as dt_tz

import httpx
import icalendar
from django.db import transaction
from django.utils import timezone

from workspace.calendar.models import Event

logger = logging.getLogger(__name__)

# Map ICS FREQ values to our RecurrenceFrequency choices
_FREQ_MAP = {
    'DAILY': 'daily',
    'WEEKLY': 'weekly',
    'MONTHLY': 'monthly',
    'YEARLY': 'yearly',
}


@transaction.atomic
def sync_external_calendar(external_calendar):
    """Fetch an ICS feed and sync events into the linked Calendar.

    - Uses ETag/If-None-Match to skip unchanged feeds.
    - Upserts events by ical_uid (create or update).
    - Deletes events whose ical_uid is no longer in the feed.
    """
    ics_text = _fetch_feed(external_calendar)

    if ics_text is None:
        # 304 Not Modified — just update timestamp
        external_calendar.last_synced_at = timezone.now()
        external_calendar.save(update_fields=['last_synced_at'])
        return

    cal = icalendar.Calendar.from_ical(ics_text)
    calendar = external_calendar.calendar
    owner = calendar.owner

    seen_uids = set()

    for component in cal.walk():
        if component.name != 'VEVENT':
            continue

        uid = str(component.get('UID', ''))
        if not uid:
            continue
        seen_uids.add(uid)

        defaults = _vevent_to_defaults(component, owner)
        try:
            existing = Event.objects.get(calendar=calendar, ical_uid=uid)
            # Only save if any field actually changed
            changed = any(
                getattr(existing, field) != value
                for field, value in defaults.items()
            )
            if changed:
                for field, value in defaults.items():
                    setattr(existing, field, value)
                existing.save()
        except Event.DoesNotExist:
            Event.objects.create(calendar=calendar, ical_uid=uid, **defaults)

    # Remove events that disappeared from the feed
    Event.objects.filter(
        calendar=calendar,
        ical_uid__isnull=False,
    ).exclude(
        ical_uid__in=seen_uids,
    ).exclude(
        ical_uid='',
    ).delete()

    external_calendar.last_synced_at = timezone.now()
    external_calendar.last_error = ''
    external_calendar.save(update_fields=['last_synced_at', 'last_etag', 'last_error'])


def _fetch_feed(external_calendar):
    """Fetch the ICS feed, returning the text or None on 304."""
    headers = {}
    if external_calendar.last_etag:
        headers['If-None-Match'] = external_calendar.last_etag

    with httpx.Client(timeout=30, follow_redirects=True) as client:
        resp = client.get(external_calendar.url, headers=headers)

    if resp.status_code == 304:
        return None

    resp.raise_for_status()
    external_calendar.last_etag = resp.headers.get('ETag', '')
    return resp.text


def _vevent_to_defaults(vevent, owner):
    """Convert a VEVENT component to a dict of Event field defaults."""
    dtstart_prop = vevent.get('DTSTART')
    dtend_prop = vevent.get('DTEND')

    return {
        'title': str(vevent.get('SUMMARY', '')),
        'description': str(vevent.get('DESCRIPTION', '')),
        'location': str(vevent.get('LOCATION', '')),
        'start': _to_datetime(dtstart_prop),
        'end': _to_datetime(dtend_prop),
        'all_day': _is_all_day(dtstart_prop),
        'ical_sequence': int(vevent.get('SEQUENCE', 0)),
        'owner': owner,
        'external_organizer': _extract_email(vevent.get('ORGANIZER')),
        **_parse_rrule(vevent),
    }


def _parse_rrule(vevent):
    """Extract recurrence fields from a VEVENT's RRULE property."""
    rrule = vevent.get('RRULE')
    if not rrule:
        return {
            'recurrence_frequency': None,
            'recurrence_interval': 1,
            'recurrence_end': None,
        }

    freq_list = rrule.get('FREQ', [])
    freq_str = freq_list[0] if freq_list else ''
    frequency = _FREQ_MAP.get(freq_str.upper())

    interval_list = rrule.get('INTERVAL', [1])
    interval = int(interval_list[0]) if interval_list else 1

    # UNTIL takes priority, then COUNT is converted to a concrete end date
    until_list = rrule.get('UNTIL', [])
    recurrence_end = None
    if until_list:
        until = until_list[0]
        if hasattr(until, 'hour'):
            recurrence_end = until if until.tzinfo else until.replace(tzinfo=dt_tz.utc)
        else:
            recurrence_end = datetime(until.year, until.month, until.day, tzinfo=dt_tz.utc)
    elif rrule.get('COUNT') and frequency:
        recurrence_end = _count_to_end(
            vevent.get('DTSTART'), frequency, interval, int(rrule['COUNT'][0]),
        )

    return {
        'recurrence_frequency': frequency,
        'recurrence_interval': interval,
        'recurrence_end': recurrence_end,
    }


def _count_to_end(dtstart_prop, frequency, interval, count):
    """Convert a COUNT-based RRULE to a concrete end datetime."""
    if not dtstart_prop or count <= 0:
        return None
    start = _to_datetime(dtstart_prop)
    if not start:
        return None

    delta_map = {
        'daily': timedelta(days=interval),
        'weekly': timedelta(weeks=interval),
        'monthly': None,
        'yearly': None,
    }
    delta = delta_map.get(frequency)
    if delta:
        return start + delta * (count - 1)

    # For monthly/yearly, approximate with dateutil
    from dateutil.relativedelta import relativedelta
    if frequency == 'monthly':
        return start + relativedelta(months=interval * (count - 1))
    if frequency == 'yearly':
        return start + relativedelta(years=interval * (count - 1))
    return None


def _to_datetime(dt_prop):
    """Convert an icalendar date/datetime property to a timezone-aware datetime."""
    if dt_prop is None:
        return None
    dt = dt_prop.dt
    if hasattr(dt, 'hour'):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_tz.utc)
        return dt
    return datetime(dt.year, dt.month, dt.day, tzinfo=dt_tz.utc)


def _is_all_day(dt_prop):
    """Return True if the DTSTART property represents an all-day event."""
    if dt_prop is None:
        return False
    return not hasattr(dt_prop.dt, 'hour')


def _extract_email(organizer_prop):
    """Extract the email address from an ICS ORGANIZER property.

    Duplicated from ics_processor._extract_email — each sync path owns
    its own parsing, and the RFC 5545 form is stable.
    """
    if not organizer_prop:
        return ''
    value = str(organizer_prop)
    if value.lower().startswith('mailto:'):
        return value[7:]
    return value
