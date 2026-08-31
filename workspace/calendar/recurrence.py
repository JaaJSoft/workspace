import logging
from datetime import UTC, datetime, timedelta

from workspace.common.logging import scrub

from .services.recurrence_rule import (
    MAX_ITERATIONS,
    is_simple_stepping,
    parse,
    simple_stepping_frequency,
)
from .services.timezones import event_timezone

logger = logging.getLogger(__name__)

_FIXED_STEP = {
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
    "hourly": timedelta(hours=1),
    "minutely": timedelta(minutes=1),
    "secondly": timedelta(seconds=1),
}

# DAILY/WEEKLY under a zone step whole local calendar days (see
# _anchored_dtstart) - the only frequencies where that math applies.
_FIXED_STEP_DAYS = {
    "daily": 1,
    "weekly": 7,
}

# Sub-day frequency proven safe to anchor by plain (absolute) timedelta
# arithmetic even under a wall-clock zone - and ONLY at INTERVAL == 1 (see
# the interval == 1 check at the call site below). Proof: swept every hour
# across both a spring-forward and a fall-back Europe/Paris transition, at
# several minute offsets, and the anchored stream matched a fresh series on
# the same phase in every case (672 combinations, zero mismatches) - see
# test_old_hourly_master_anchor_matches_unanchored_across_dst. No interval
# other than 1 has an equivalent proof, so HOURLY at any other interval,
# MINUTELY at any interval, and SECONDLY at any interval all fall back to
# the true start under a zone and rely on the skip budget in
# occurrences_in_range instead - see
# test_old_hourly_interval_master_matches_its_own_true_walk for why that
# fallback, not a wider anchor, is the correct default absent a proof. All
# of them still anchor under UTC (tz is None), where there is no DST to get
# wrong.
_ABSOLUTE_STEP_UNDER_TZ = {"hourly"}


def _anchored_dtstart(master, floor, tz, frequency, interval):
    """Return the series dtstart advanced to the last in-phase occurrence
    at or before *floor*.

    Iterating an rrule walks the series occurrence by occurrence from dtstart,
    so a years-old daily master costs hundreds of discarded iterations per
    expansion. Re-anchoring dtstart keeps the exact same occurrence stream
    while skipping the pre-window walk.

    Without *tz* (legacy UTC series) every fixed-step frequency anchors by
    plain timedelta arithmetic - there is no DST to get wrong. With *tz*,
    DAILY/WEEKLY anchor by stepping whole local calendar days and
    reattaching the original local time (a fixed timedelta would drift
    across DST transitions); HOURLY at INTERVAL == 1 anchors the same way as
    the tz-less case (proven equivalent for exactly that one combination,
    see ``_ABSOLUTE_STEP_UNDER_TZ``). Every other zoned combination - HOURLY
    at any other interval, MINUTELY at any interval, SECONDLY at any
    interval, and everything ``is_simple_stepping`` rejects (monthly,
    yearly, BY- and COUNT-qualified rules) - keeps the true start instead.
    """
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

    if frequency in _ABSOLUTE_STEP_UNDER_TZ and interval == 1 and dtstart < floor:
        step = _FIXED_STEP[frequency] * interval
        dtstart += ((floor - dtstart) // step) * step
    return dtstart


def _anchor(master, floor, tz):
    """Series dtstart, advanced to just before *floor* when that is safe.

    Walking an rrule from a years-old dtstart costs hundreds of discarded
    iterations per expansion, so re-anchoring is worth keeping. The algebra
    assumes a fixed timedelta step, which only holds for DAILY, WEEKLY,
    HOURLY, MINUTELY and SECONDLY rules with no BY parts and no COUNT (COUNT
    is measured from dtstart, so moving it forward would fabricate
    occurrences past the real end of the series) - everything else keeps the
    true start and is bounded by calendar stepping, or the skip budget in
    ``occurrences_in_range``, anyway.
    """
    if not is_simple_stepping(master.recurrence_rule):
        return master.start
    components = simple_stepping_frequency(master.recurrence_rule)
    if components is None:
        return master.start
    frequency, interval = components
    return _anchored_dtstart(master, floor, tz, frequency, interval)


def _iteration_cap_warning(master, phase):
    logger.warning(
        "Recurrence rule exceeded %d iterations while %s, truncating: %s",
        MAX_ITERATIONS,
        phase,
        scrub(master.recurrence_rule),
    )


def occurrences_in_range(master, range_start, range_end):
    """Yield occurrence start datetimes (aware UTC) overlapping the window.

    Two independent MAX_ITERATIONS budgets guard the walk: one for
    candidates before the window floor, one for candidates at or after it.
    Splitting them matters - a series the anchor could not reach
    (BY-qualified, monthly, yearly, or a fixed-step rule anchoring does not
    cover) still pays for walking its own history, and that walk must not
    steal budget from the in-window occurrences the caller actually wants.
    Only a series genuinely too dense to reach the window even after
    anchoring trips the first budget; only a series genuinely too dense
    inside the window trips the second - a hostile feed-supplied
    FREQ=SECONDLY with no UNTIL/COUNT is the case that really does trip the
    second one.
    """
    duration = (master.end - master.start) if master.end else None
    # An occurrence starting before the window can still spill into it.
    window_floor = (range_start - duration) if duration else range_start

    tz = event_timezone(master)
    rule = parse(master.recurrence_rule, _anchor(master, window_floor, tz), tz)
    if rule is None:
        return

    skipped = 0
    considered = 0
    for dt in rule:
        if dt < window_floor:
            skipped += 1
            if skipped > MAX_ITERATIONS:
                _iteration_cap_warning(master, "skipping to the window")
                return
            continue
        if dt >= range_end:
            break
        considered += 1
        if considered > MAX_ITERATIONS:
            _iteration_cap_warning(master, "expanding the window")
            return
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
    would otherwise be walked forever. Unlike ``occurrences_in_range``, one
    budget is enough here - ``rule.xafter`` already does its own skipping to
    *after* inside dateutil, before anything reaches this loop.
    """
    tz = event_timezone(master)
    rule = parse(master.recurrence_rule, _anchor(master, after, tz), tz)
    if rule is None:
        return
    for index, dt in enumerate(rule.xafter(after, count=limit, inc=True)):
        if index >= MAX_ITERATIONS:
            _iteration_cap_warning(master, "expanding the window")
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
