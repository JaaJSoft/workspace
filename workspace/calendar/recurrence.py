from datetime import UTC, datetime, timedelta

from dateutil.rrule import DAILY, MONTHLY, WEEKLY, YEARLY, rrule
from django.core.exceptions import ObjectDoesNotExist

from .services.timezones import event_timezone

FREQ_MAP = {
    "daily": DAILY,
    "weekly": WEEKLY,
    "monthly": MONTHLY,
    "yearly": YEARLY,
}


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

    Iterating an rrule walks the series occurrence by occurrence from
    dtstart, so a years-old daily master costs hundreds of discarded
    iterations per expansion. Re-anchoring dtstart keeps the exact same
    occurrence stream while skipping the pre-window walk.

    Without *tz* (legacy UTC series) the phase is plain timedelta
    arithmetic. With *tz* the series is anchored to a local wall clock, so
    the anchor steps whole local calendar days and reattaches the original
    local time - a fixed timedelta would drift across DST transitions.
    Monthly/yearly steps are calendar-dependent (day-31 or Feb-29 masters
    skip periods), so those keep the true start - they are bounded to at
    most 12 iterations per year of series age.
    """
    if tz is None:
        dtstart = master.start
        fixed_step = _FIXED_STEP.get(master.recurrence_frequency)
        if fixed_step and dtstart < floor:
            step = fixed_step * master.recurrence_interval
            dtstart += ((floor - dtstart) // step) * step
        return dtstart

    dtstart = master.start.astimezone(tz)
    step_days = _FIXED_STEP_DAYS.get(master.recurrence_frequency)
    if step_days and dtstart < floor:
        step = step_days * master.recurrence_interval
        days = (floor.astimezone(tz).date() - dtstart.date()).days
        if days > 0:
            anchored_date = dtstart.date() + timedelta(days=(days // step) * step)
            dtstart = datetime.combine(anchored_date, dtstart.time(), tzinfo=tz)
    return dtstart


def _series_rrule(master, dtstart, until):
    return rrule(
        FREQ_MAP[master.recurrence_frequency],
        interval=master.recurrence_interval,
        dtstart=dtstart,
        until=until,
    )


def _build_rrule(master, range_start, range_end):
    """Yield occurrence start datetimes (aware UTC) for a recurring master.

    Series with an ``Event.timezone`` iterate in that zone, preserving the
    local wall clock across DST transitions; legacy series iterate in UTC
    with fixed steps. Either way the yielded instants are UTC, so exception
    keys and serialized values stay zone-independent.
    """
    if FREQ_MAP.get(master.recurrence_frequency) is None:
        return

    until = range_end
    if master.recurrence_end and master.recurrence_end < until:
        until = master.recurrence_end

    duration = (master.end - master.start) if master.end else None

    # First occurrence start that could still overlap the window: with a
    # duration, an occurrence starting before range_start can spill into
    # the window, so back the threshold up by the duration.
    window_floor = (range_start - duration) if duration else range_start

    tz = event_timezone(master)
    rule = _series_rrule(master, _anchored_dtstart(master, window_floor, tz), until)

    for dt in rule:
        if dt >= range_end:
            break
        occ = dt.astimezone(UTC) if tz else dt
        # Only yield if the occurrence overlaps the query range (strict:
        # an occurrence ending exactly at range_start does not overlap).
        if duration:
            if occ + duration > range_start:
                yield occ
        else:
            if occ >= range_start:
                yield occ


def next_occurrences_after(master, after, limit=None):
    """Yield occurrence start datetimes (aware UTC) for a recurring master,
    all with start >= after.

    Unlike `_build_rrule`, this is count-bounded (not range-bounded). A master
    whose own `start` is in the past is still iterated — only the occurrences
    before `after` are skipped. Stops early at `master.recurrence_end`.

    If `limit` is None, yields all remaining occurrences (bounded only by
    `master.recurrence_end`). This allows callers that need to filter the
    stream (e.g. skipping exceptions) to take as many occurrences as they
    need rather than being capped.
    """
    if FREQ_MAP.get(master.recurrence_frequency) is None:
        return

    tz = event_timezone(master)
    rule = _series_rrule(
        master,
        # xafter still walks the series from dtstart before yielding, so
        # jump the anchor to just before `after` (see _anchored_dtstart).
        _anchored_dtstart(master, after, tz),
        master.recurrence_end,  # None → unbounded
    )
    for dt in rule.xafter(after, count=limit, inc=True):
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


def meeting_join_url(event, request=None):
    """Absolute join URL for the meeting attached to *event*, or None.

    Shared by the event serializer, the server-rendered event card, and the
    recurring-occurrence dict builders below - a recurring series' meeting
    is one row per series, attached to the master event (see ``Meeting`` in
    ``workspace.chat.models``), so callers pass whichever row holds it: the
    event itself, or an exception's ``recurrence_parent``.
    """
    try:
        meeting = event.meeting
    except ObjectDoesNotExist:
        return None
    return (
        request.build_absolute_uri(meeting.join_path) if request else meeting.join_path
    )


def make_virtual_occurrence(master, occ_start, request=None):
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
        "join_url": meeting_join_url(master, request),
        "owner": _user_dict(master.owner),
        "members": getattr(master, "_cached_member_dicts", None)
        or [_member_dict(m) for m in master.members.all()],
        "created_at": master.created_at.isoformat(),
        "updated_at": master.updated_at.isoformat(),
        "is_recurring": True,
        "is_exception": False,
        "master_event_id": str(master.uuid),
        "original_start": occ_start.isoformat(),
        "recurrence_frequency": master.recurrence_frequency,
        "recurrence_interval": master.recurrence_interval,
        "recurrence_end": master.recurrence_end.isoformat()
        if master.recurrence_end
        else None,
    }


def make_exception_dict(exc, request=None):
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
        "join_url": meeting_join_url(exc.recurrence_parent, request)
        if exc.recurrence_parent
        else None,
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
        "recurrence_frequency": exc.recurrence_parent.recurrence_frequency
        if exc.recurrence_parent
        else None,
        "recurrence_interval": exc.recurrence_parent.recurrence_interval
        if exc.recurrence_parent
        else 1,
        "recurrence_end": exc.recurrence_parent.recurrence_end.isoformat()
        if exc.recurrence_parent and exc.recurrence_parent.recurrence_end
        else None,
    }


def expand_recurring_events(masters_qs, range_start, range_end, request=None):
    """
    Expand recurring master events into occurrence dicts.
    Substitutes materialized exceptions, skips cancelled ones.
    """
    from django.db.models import Prefetch

    from .models import Event, EventMember

    master_ids = [m.uuid for m in masters_qs]
    if not master_ids:
        return []

    # Fetch all exceptions for these masters, prefetch members. The
    # exception's own join_url reads the master's meeting through
    # recurrence_parent, so that relation is select_related too.
    exceptions = (
        Event.objects.filter(recurrence_parent_id__in=master_ids)
        .select_related(
            "owner", "calendar", "recurrence_parent", "recurrence_parent__meeting"
        )
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
        for occ_start in _build_rrule(master, range_start, range_end):
            key = (master.uuid, occ_start)
            exc = exc_index.get(key)
            if exc:
                if exc.is_cancelled:
                    continue  # Skip cancelled occurrences
                occurrences.append(make_exception_dict(exc, request))
            else:
                occurrences.append(make_virtual_occurrence(master, occ_start, request))

    return occurrences
