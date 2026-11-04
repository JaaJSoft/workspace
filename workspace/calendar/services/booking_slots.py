"""Free slots of a booking page.

A slot is free when it fits in the page's weekly hours, respects the notice
and the horizon, and nothing of the owner's - an event on one of the page's
calendars, an invitation they have not declined, an appointment booked through
any of their pages - comes within ``buffer_minutes`` of it.

Only instants leave this module: the public side must never learn what made a
slot busy, so the busy intervals stay internal.
"""

from bisect import bisect_left
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db.models import Q
from django.utils import timezone

from ..models import Event, EventMember
from ..recurrence import occurrences_in_range

# The widest range a single slots request may cover: a month view with the
# weeks it overflows into.
MAX_RANGE = timedelta(days=42)

# All-day rows are UTC-midnight day labels, and the local day they block can
# start up to 14 hours away from that instant: the database is queried with
# this much margin and the exact overlap is decided in Python.
_QUERY_MARGIN = timedelta(days=1)


def page_zone(page):
    """The zone the page's weekly hours are written in."""
    try:
        return ZoneInfo(page.timezone)
    except ZoneInfoNotFoundError, ValueError, KeyError:
        return UTC


def candidate_slots(page, lo, hi):
    """Starts of the weekly-hours slots in ``[lo, hi)``, conflicts ignored.

    Each range is laid out on the wall clock of its own day, so 9:00 stays
    9:00 on both sides of a DST change, and stepped through in absolute time
    from its start.
    """
    zone = page_zone(page)
    duration = timedelta(minutes=page.duration_minutes)
    day = lo.astimezone(zone).date()
    last_day = hi.astimezone(zone).date()
    while day <= last_day:
        for start_text, end_text in page.weekly_hours[day.weekday()]:
            slot = datetime.combine(
                day, time.fromisoformat(start_text), tzinfo=zone
            ).astimezone(UTC)
            range_end = datetime.combine(
                day, time.fromisoformat(end_text), tzinfo=zone
            ).astimezone(UTC)
            while slot + duration <= range_end:
                if lo <= slot < hi:
                    yield slot
                slot += duration
        day += timedelta(days=1)


def _local_midnight(day, zone):
    return datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)


def _interval(start, end, all_day, zone):
    """The instants an event occupies, read in the page's zone.

    An all-day event takes whole local days of the page: the day its label
    names, through the day before its (exclusive) end label.
    """
    if all_day:
        first = start.astimezone(UTC).date()
        days = 1
        if end is not None:
            days = max(1, (end.astimezone(UTC).date() - first).days)
        return (
            _local_midnight(first, zone),
            _local_midnight(first + timedelta(days=days), zone),
        )
    if end is None or end < start:
        return (start, start)
    return (start, end)


def _owner_events_q(page):
    """The events whose time belongs to the page owner."""
    calendar_ids = {
        page.calendar_id,
        *page.busy_calendars.values_list("uuid", flat=True),
    }
    attending = (
        EventMember.objects.filter(user_id=page.owner_id)
        .exclude(status=EventMember.Status.DECLINED)
        .values_list("event_id", flat=True)
    )
    return (
        Q(calendar_id__in=calendar_ids)
        | Q(uuid__in=attending)
        # Appointments booked through any of the owner's pages, whichever
        # calendar they landed on: two pages share one person's time.
        | Q(owner_id=page.owner_id, source=Event.Source.BOOKING)
    )


def busy_intervals(page, lo, hi):
    """``(start, end)`` pairs, aware UTC, of the owner's time taken around ``[lo, hi)``."""
    zone = page_zone(page)
    owner_q = _owner_events_q(page)
    query_lo = lo - _QUERY_MARGIN
    query_hi = hi + _QUERY_MARGIN

    intervals = []
    # Single events and the materialized exceptions of a series (moved
    # occurrences, at their new time).
    for start, end, all_day in (
        Event.objects.filter(owner_q, is_cancelled=False, is_recurring=False)
        .filter(start__lt=query_hi)
        .filter(Q(end__gt=query_lo) | Q(end__isnull=True, start__gte=query_lo))
        .values_list("start", "end", "all_day")
    ):
        intervals.append(_interval(start, end, all_day, zone))

    masters = list(
        Event.objects.filter(
            owner_q,
            is_cancelled=False,
            is_recurring=True,
            recurrence_parent__isnull=True,
            start__lt=query_hi,
        ).filter(Q(recurrence_until__isnull=True) | Q(recurrence_until__gte=query_lo))
    )
    if masters:
        # An occurrence an exception replaces is either cancelled or already
        # counted above at its new time.
        replaced = set(
            Event.objects.filter(
                recurrence_parent_id__in=[m.uuid for m in masters],
                original_start__isnull=False,
            ).values_list("recurrence_parent_id", "original_start")
        )
        for master in masters:
            duration = (master.end - master.start) if master.end else None
            for occurrence in occurrences_in_range(master, query_lo, query_hi):
                if (master.uuid, occurrence) in replaced:
                    continue
                end = occurrence + duration if duration else None
                intervals.append(_interval(occurrence, end, master.all_day, zone))
    return intervals


def _merged(intervals):
    """Sorted, disjoint union of *intervals*."""
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1][1] = end
        else:
            merged.append([start, end])
    return merged


def free_slots(page, lo, hi, *, now=None):
    """Starts, aware UTC, of the slots of *page* a guest may book in ``[lo, hi)``."""
    now = now or timezone.now()
    lo = max(lo, now + timedelta(hours=page.min_notice_hours))
    hi = min(hi, now + timedelta(days=page.days_ahead))
    if lo >= hi:
        return []
    candidates = list(candidate_slots(page, lo, hi))
    if not candidates:
        return []

    duration = timedelta(minutes=page.duration_minutes)
    buffer = timedelta(minutes=page.buffer_minutes)
    busy = _merged(
        busy_intervals(page, candidates[0] - buffer, candidates[-1] + duration + buffer)
    )
    busy_starts = [start for start, _end in busy]

    free = []
    for slot in candidates:
        wanted_start = slot - buffer
        wanted_end = slot + duration + buffer
        # The last busy interval starting before the wanted range ends is the
        # only one that can reach into it: the intervals are disjoint.
        index = bisect_left(busy_starts, wanted_end) - 1
        if index >= 0 and busy[index][1] > wanted_start:
            continue
        free.append(slot)
    return free


def is_bookable(page, start, *, now=None):
    """Whether *start* is one of the free slots of *page* right now."""
    return start in free_slots(page, start, start + timedelta(microseconds=1), now=now)
