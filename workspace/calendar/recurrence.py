import logging
from datetime import UTC, datetime, timedelta

from workspace.common.logging import scrub

from .services.recurrence_rule import (
    MAX_ITERATIONS,
    is_simple_stepping,
    parse,
    to_simple,
)
from .services.timezones import event_timezone

logger = logging.getLogger(__name__)

_FIXED_STEP = {
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
}

_FIXED_STEP_DAYS = {
    "daily": 1,
    "weekly": 7,
}


def _anchored_dtstart(master, floor, tz=None):
    """Return the series dtstart advanced to the last in-phase occurrence
    at or before *floor*.

    Iterating an rrule walks the series occurrence by occurrence from dtstart,
    so a years-old daily master costs hundreds of discarded iterations per
    expansion. Re-anchoring dtstart keeps the exact same occurrence stream
    while skipping the pre-window walk.

    Without *tz* (legacy UTC series) the phase is plain timedelta arithmetic.
    With *tz* the series is anchored to a local wall clock, so the anchor steps
    whole local calendar days and reattaches the original local time - a fixed
    timedelta would drift across DST transitions. Callers must gate this on
    ``is_simple_stepping``; monthly, yearly and BY-qualified rules step by the
    calendar and have no constant phase to solve for.
    """
    simple = to_simple(master.recurrence_rule) or {}
    frequency = simple.get("frequency")
    interval = simple.get("interval", 1)

    if tz is None:
        dtstart = master.start
        fixed_step = _FIXED_STEP.get(frequency)
        if fixed_step and dtstart < floor:
            step = fixed_step * interval
            dtstart += ((floor - dtstart) // step) * step
        return dtstart

    dtstart = master.start.astimezone(tz)
    step_days = _FIXED_STEP_DAYS.get(frequency)
    if step_days and dtstart < floor:
        step = step_days * interval
        days = (floor.astimezone(tz).date() - dtstart.date()).days
        if days > 0:
            anchored_date = dtstart.date() + timedelta(days=(days // step) * step)
            dtstart = datetime.combine(anchored_date, dtstart.time(), tzinfo=tz)
    return dtstart


def _anchor(master, floor, tz):
    """Series dtstart, advanced to just before *floor* when that is safe.

    Walking an rrule from a years-old dtstart costs hundreds of discarded
    iterations per expansion, so re-anchoring is worth keeping. The algebra
    assumes a fixed timedelta step, which only holds for DAILY and WEEKLY rules
    with no BY parts and no COUNT (COUNT is measured from dtstart, so moving it
    forward would fabricate occurrences past the series' real end) -
    everything else keeps the true start and is bounded by calendar stepping
    anyway.
    """
    if not is_simple_stepping(master.recurrence_rule):
        return master.start
    return _anchored_dtstart(master, floor, tz)


def _iteration_cap_warning(master):
    logger.warning(
        "Recurrence rule exceeded %d iterations, truncating: %s",
        MAX_ITERATIONS,
        scrub(master.recurrence_rule),
    )


def occurrences_in_range(master, range_start, range_end):
    """Yield occurrence start datetimes (aware UTC) overlapping the window.

    Stops after MAX_ITERATIONS candidates even if the window is still open,
    so a dense feed-supplied rule (FREQ=SECONDLY with no UNTIL/COUNT) that
    slips past the query-layer prune degrades to a truncated series instead
    of exhausting the request.
    """
    duration = (master.end - master.start) if master.end else None
    # An occurrence starting before the window can still spill into it.
    window_floor = (range_start - duration) if duration else range_start

    tz = event_timezone(master)
    rule = parse(master.recurrence_rule, _anchor(master, window_floor, tz), tz)
    if rule is None:
        return

    for index, dt in enumerate(rule):
        if index >= MAX_ITERATIONS:
            _iteration_cap_warning(master)
            return
        if dt >= range_end:
            break
        occ = dt.astimezone(UTC) if tz else dt
        if duration:
            if occ + duration > range_start:
                yield occ
        elif occ >= range_start:
            yield occ


def next_occurrences_after(master, after, limit=None):
    """Yield occurrence starts (aware UTC) at or after *after*, count-bounded.

    If `limit` is None, yields all remaining occurrences bounded only by the
    series' own end (its UNTIL/COUNT, if any) - callers that need to filter
    the stream (e.g. skipping exceptions) can take as many as they need
    rather than being capped up front. Either way, stops after MAX_ITERATIONS
    candidates: an unbounded feed-supplied rule with no caller-side limit
    would otherwise be walked forever.
    """
    tz = event_timezone(master)
    rule = parse(master.recurrence_rule, _anchor(master, after, tz), tz)
    if rule is None:
        return
    for index, dt in enumerate(rule.xafter(after, count=limit, inc=True)):
        if index >= MAX_ITERATIONS:
            _iteration_cap_warning(master)
            return
        yield dt.astimezone(UTC) if tz else dt


def _user_dict(user):
    return {
        "id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
    }


def _member_dict(member):
    return {
        "uuid": str(member.uuid),
        "user": _user_dict(member.user),
        "status": member.status,
        "created_at": member.created_at.isoformat(),
    }


def _event_dt_str(dt, all_day):
    """ISO instant for timed values, date-only day label for all-day ones."""
    if dt is None:
        return None
    if all_day:
        return dt.astimezone(UTC).date().isoformat()
    return dt.isoformat()


def make_virtual_occurrence(master, occ_start):
    """Build a dict for a virtual (non-materialized) occurrence."""
    duration = (master.end - master.start) if master.end else None
    occ_end = (occ_start + duration) if duration else None

    return {
        "uuid": f"{master.uuid}:{occ_start.isoformat()}",
        "calendar_id": str(master.calendar_id),
        "title": master.title,
        "description": master.description,
        "start": _event_dt_str(occ_start, master.all_day),
        "end": _event_dt_str(occ_end, master.all_day),
        "all_day": master.all_day,
        "location": master.location,
        "owner": _user_dict(master.owner),
        "members": getattr(master, "_cached_member_dicts", None)
        or [_member_dict(m) for m in master.members.all()],
        "created_at": master.created_at.isoformat(),
        "updated_at": master.updated_at.isoformat(),
        "is_recurring": True,
        "is_exception": False,
        "master_event_id": str(master.uuid),
        "original_start": occ_start.isoformat(),
        "recurrence_rule": master.recurrence_rule,
    }


def make_exception_dict(exc):
    """Convert a materialized exception Event to the occurrence dict format."""
    return {
        "uuid": str(exc.uuid),
        "calendar_id": str(exc.calendar_id),
        "title": exc.title,
        "description": exc.description,
        "start": _event_dt_str(exc.start, exc.all_day),
        "end": _event_dt_str(exc.end, exc.all_day),
        "all_day": exc.all_day,
        "location": exc.location,
        "owner": _user_dict(exc.owner),
        "members": [_member_dict(m) for m in exc.members.all()],
        "created_at": exc.created_at.isoformat(),
        "updated_at": exc.updated_at.isoformat(),
        "is_recurring": True,
        "is_exception": True,
        "master_event_id": str(exc.recurrence_parent_id),
        "original_start": exc.original_start.isoformat()
        if exc.original_start
        else None,
        "recurrence_rule": exc.recurrence_parent.recurrence_rule
        if exc.recurrence_parent
        else "",
    }


def expand_recurring_events(masters_qs, range_start, range_end):
    """
    Expand recurring master events into occurrence dicts.
    Substitutes materialized exceptions, skips cancelled ones.
    """
    from django.db.models import Prefetch

    from .models import Event, EventMember

    master_ids = [m.uuid for m in masters_qs]
    if not master_ids:
        return []

    # Fetch all exceptions for these masters, prefetch members
    exceptions = (
        Event.objects.filter(recurrence_parent_id__in=master_ids)
        .select_related("owner", "calendar", "recurrence_parent")
        .prefetch_related(
            Prefetch("members", queryset=EventMember.objects.select_related("user"))
        )
    )

    # Index by (parent_id, original_start). UUIDs and datetimes are both
    # hashable, so we use them directly — skipping .isoformat() avoids a
    # pair of string allocations per key on both the indexing side and the
    # lookup side below.
    exc_index = {}
    for exc in exceptions:
        if exc.original_start:
            key = (exc.recurrence_parent_id, exc.original_start)
            exc_index[key] = exc

    occurrences = []
    for master in masters_qs:
        # Pre-compute members list once per master (reused across all virtual occurrences)
        master._cached_member_dicts = [_member_dict(m) for m in master.members.all()]
        for occ_start in occurrences_in_range(master, range_start, range_end):
            key = (master.uuid, occ_start)
            exc = exc_index.get(key)
            if exc:
                if exc.is_cancelled:
                    continue  # Skip cancelled occurrences
                occurrences.append(make_exception_dict(exc))
            else:
                occurrences.append(make_virtual_occurrence(master, occ_start))

    return occurrences
