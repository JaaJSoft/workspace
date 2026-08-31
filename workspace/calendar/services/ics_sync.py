"""Fetch and sync external ICS calendar feeds."""

import logging

import httpx2
import icalendar
from django.db import transaction
from django.utils import timezone

from workspace.calendar.models import Event
from workspace.calendar.services.ics_common import (
    extract_email,
    is_all_day,
    parse_dt_prop,
)
from workspace.calendar.services.recurrence_rule import derive_into_defaults
from workspace.calendar.services.timezones import normalize_all_day
from workspace.users.services.settings import get_user_timezone

logger = logging.getLogger(__name__)


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
        derive_into_defaults(defaults)
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

    with httpx2.Client(timeout=30, follow_redirects=True) as client:
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
        "recurrence_rule": _recurrence_lines(vevent),
    }


def _recurrence_lines(vevent):
    """Return the VEVENT's recurrence lines as the feed wrote them.

    Storing the rule rather than a summary of it is the point: flattening
    COUNT into a date and dropping BY parts is what turned an imported
    "second Tuesday of the month" into a plain "monthly".

    icalendar returns a bare value for a property that appears once and a
    list when it repeats, so both shapes have to be handled.

    The property parameters are part of the value, not decoration: without the
    TZID an RDATE's wall-clock time is read in the wrong zone, and without
    VALUE=DATE a bare date is not a date any more. Re-emitting them keeps the
    stored text the text the feed sent.
    """
    lines = []
    for name in ("RRULE", "RDATE", "EXDATE"):
        value = vevent.get(name)
        if value is None:
            continue
        for item in value if isinstance(value, list) else [value]:
            params = item.params.to_ical().decode()
            prefix = f"{name};{params}" if params else name
            lines.append(f"{prefix}:{item.to_ical().decode()}")
    return "\n".join(lines)


def external_calendars_with_errors():
    """Active external calendars whose last sync recorded an error."""
    from workspace.calendar.models_external import ExternalCalendar

    return ExternalCalendar.objects.filter(is_active=True).exclude(last_error="")


def queue_external_calendar_syncs(externals):
    """Queue a background sync for the active feeds in *externals*.

    Returns the number of syncs queued. Manual dispatches carry no claim
    token (see ``calendar.sync_external_calendar``).
    """
    from workspace.calendar.tasks import sync_external_calendar_task

    count = 0
    for ext in externals.filter(is_active=True):
        sync_external_calendar_task.delay(str(ext.uuid))
        count += 1
    return count


def clear_sync_errors(externals):
    """Blank ``last_error`` on *externals*; returns the number of rows updated."""
    return externals.exclude(last_error="").update(last_error="")
