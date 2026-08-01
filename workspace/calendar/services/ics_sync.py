"""Fetch and sync external ICS calendar feeds."""

import logging
from datetime import UTC, datetime

import httpx
import icalendar
from django.db import transaction
from django.utils import timezone

from workspace.calendar.models import Event
from workspace.calendar.services.ics_common import (
    extract_email,
    is_all_day,
    parse_dt_prop,
)
from workspace.calendar.services.timezones import normalize_all_day
from workspace.users.services.settings import get_user_timezone

logger = logging.getLogger(__name__)

# Map ICS FREQ values to our RecurrenceFrequency choices
_FREQ_MAP = {
    "DAILY": "daily",
    "WEEKLY": "weekly",
    "MONTHLY": "monthly",
    "YEARLY": "yearly",
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
        external_calendar.save(update_fields=["last_synced_at"])
        return

    cal = icalendar.Calendar.from_ical(ics_text)
    calendar = external_calendar.calendar
    owner = calendar.owner
    # Floating (naive) feed times mean "local time of the observer": the
    # closest observer we have is the calendar owner.
    owner_tz = get_user_timezone(owner)

    # Pre-load existing rows keyed by ical_uid so we can short-circuit
    # no-op syncs: feeds that don't support ETag/If-None-Match return
    # 200 on every poll, and without this check ``update_or_create``
    # would rewrite every row (and advance ``updated_at``) every 15 min
    # even when the upstream content is unchanged.
    existing_by_uid = {
        e.ical_uid: e
        for e in Event.objects.filter(
            calendar=calendar,
            ical_uid__isnull=False,
        ).exclude(ical_uid="")
    }

    seen_uids = set()

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        uid = str(component.get("UID", ""))
        if not uid:
            continue
        seen_uids.add(uid)

        defaults = _vevent_to_defaults(component, owner, owner_tz)
        existing = existing_by_uid.get(uid)
        if existing is not None and _matches_defaults(existing, defaults):
            continue

        # update_or_create + the partial UniqueConstraint on (calendar,
        # ical_uid) is atomic under concurrent sync runs: the loser of an
        # INSERT race transparently falls back to UPDATE instead of
        # raising IntegrityError or creating a duplicate row.
        Event.objects.update_or_create(
            calendar=calendar,
            ical_uid=uid,
            defaults=defaults,
        )

    # Remove events that disappeared from the feed
    Event.objects.filter(
        calendar=calendar,
        ical_uid__isnull=False,
    ).exclude(
        ical_uid__in=seen_uids,
    ).exclude(
        ical_uid="",
    ).delete()

    external_calendar.last_synced_at = timezone.now()
    external_calendar.last_error = ""
    external_calendar.save(update_fields=["last_synced_at", "last_etag", "last_error"])


def _fetch_feed(external_calendar):
    """Fetch the ICS feed, returning the text or None on 304."""
    headers = {}
    if external_calendar.last_etag:
        headers["If-None-Match"] = external_calendar.last_etag

    with httpx.Client(timeout=30, follow_redirects=True) as client:
        resp = client.get(external_calendar.url, headers=headers)

    if resp.status_code == 304:
        return None

    resp.raise_for_status()
    external_calendar.last_etag = resp.headers.get("ETag", "")
    return resp.text


def _matches_defaults(existing, defaults):
    """Return True if every default field on ``existing`` already matches.

    ``defaults`` contains the ``owner`` User instance; compare via its pk
    against ``existing.owner_id`` so we don't trigger a lazy FK lookup.
    """
    for key, value in defaults.items():
        if key == "owner":
            if existing.owner_id != value.pk:
                return False
        elif getattr(existing, key) != value:
            return False
    return True


def _vevent_to_defaults(vevent, owner, owner_tz=None):
    """Convert a VEVENT component to a dict of Event field defaults."""
    dtstart_prop = vevent.get("DTSTART")
    dtend_prop = vevent.get("DTEND")

    start, tzid = parse_dt_prop(dtstart_prop, owner_tz)
    end, _end_tzid = parse_dt_prop(dtend_prop, owner_tz)
    all_day = is_all_day(dtstart_prop)
    if all_day:
        start = normalize_all_day(start)
        end = normalize_all_day(end)
        tzid = ""

    return {
        "title": str(vevent.get("SUMMARY", "")),
        "description": str(vevent.get("DESCRIPTION", "")),
        "location": str(vevent.get("LOCATION", "")),
        "start": start,
        "end": end,
        "all_day": all_day,
        "timezone": tzid,
        "ical_sequence": int(vevent.get("SEQUENCE", 0)),
        "owner": owner,
        "external_organizer": extract_email(vevent.get("ORGANIZER")),
        **_parse_rrule(vevent, start, tzid),
    }


def _parse_rrule(vevent, start, tzid):
    """Extract recurrence fields from a VEVENT's RRULE property."""
    rrule = vevent.get("RRULE")
    if not rrule:
        return {
            "recurrence_frequency": None,
            "recurrence_interval": 1,
            "recurrence_end": None,
        }

    freq_list = rrule.get("FREQ", [])
    freq_str = freq_list[0] if freq_list else ""
    frequency = _FREQ_MAP.get(freq_str.upper())

    interval_list = rrule.get("INTERVAL", [1])
    interval = int(interval_list[0]) if interval_list else 1

    # UNTIL takes priority, then COUNT is converted to a concrete end date
    until_list = rrule.get("UNTIL", [])
    recurrence_end = None
    if until_list:
        until = until_list[0]
        if hasattr(until, "hour"):
            recurrence_end = until if until.tzinfo else until.replace(tzinfo=UTC)
        else:
            recurrence_end = datetime(until.year, until.month, until.day, tzinfo=UTC)
    elif rrule.get("COUNT") and frequency:
        recurrence_end = _count_to_end(
            start, frequency, interval, int(rrule["COUNT"][0]), tzid
        )

    return {
        "recurrence_frequency": frequency,
        "recurrence_interval": interval,
        "recurrence_end": recurrence_end,
    }


def _count_to_end(start, frequency, interval, count, tzid=""):
    """Convert a COUNT-based RRULE to the exact last-occurrence instant.

    Computed with the same dateutil rrule the expansion engine uses (in
    the event's zone when it has one), so the final occurrence survives
    DST transitions and calendar-dependent monthly/yearly stepping.
    """
    if not start or count <= 0:
        return None

    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    from dateutil.rrule import rrule as du_rrule

    from workspace.calendar.recurrence import FREQ_MAP as _ENGINE_FREQ_MAP

    freq = _ENGINE_FREQ_MAP.get(frequency)
    if freq is None:
        return None
    dtstart = start
    if tzid:
        try:
            dtstart = start.astimezone(ZoneInfo(tzid))
        except ZoneInfoNotFoundError, KeyError, ValueError:
            pass
    rule = du_rrule(freq, interval=interval, dtstart=dtstart, count=count)
    return rule[count - 1].astimezone(UTC)
