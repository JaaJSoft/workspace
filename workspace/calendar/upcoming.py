"""Upcoming events helper that expands recurring occurrences.

Used by the dashboard event list and the today's-events notification cron.
"""

from datetime import UTC, timedelta

from dateutil.parser import parse as _parse_dt
from django.db.models import Q
from django.utils import timezone

from workspace.calendar.models import Event, EventMember
from workspace.calendar.queries import visible_events_q
from workspace.calendar.recurrence import occurrences_in_range
from workspace.calendar.services.timezones import all_day_label
from workspace.common.datetimes import local_date_range


def event_instant(value):
    """Return the aware instant a serialized event ``start``/``end`` stands for.

    A timed event serializes as an aware ISO string. An all-day event
    serializes as the bare UTC date of its day label, which parses naive;
    UTC midnight is the instant that date stands for.
    """
    dt = _parse_dt(value)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class VirtualOccurrence:
    """Lightweight event-like object for recurring occurrences in templates.

    ``master`` carries the real Event row, for consumers that need a
    database-addressable object behind the synthetic uuid (the notification
    cron keys its rows on it).
    """

    __slots__ = ("uuid", "title", "all_day", "start", "calendar", "master")

    def __init__(self, master, occ_start):
        self.uuid = f"{master.uuid}:{occ_start.isoformat()}"
        self.title = master.title
        self.all_day = master.all_day
        self.start = occ_start
        self.calendar = master.calendar
        self.master = master


def get_upcoming_for_user(user, now, tz=None):
    """Return the user's events still relevant today, recurring occurrences included.

    "Today" is the local day of *now* in *tz* (the active timezone by
    default). Timed events count until they end; all-day events count for
    the whole day their label names, matched against the UTC-midnight
    storage rather than the local day's instants (see ``all_day_label``).

    Returns a list mixing real Event instances and VirtualOccurrence
    objects (both expose .uuid, .title, .all_day, .start, .calendar),
    all-day entries first, then by start.
    """
    if tz is None:
        tz = timezone.get_current_timezone()
    today = now.astimezone(tz).date()
    _, end_of_today = local_date_range(today, today, tz)
    day_label = all_day_label(today)
    next_label = day_label + timedelta(days=1)

    user_q = visible_events_q(user)
    # Exclude events the user explicitly declined
    declined_q = Q(members__user=user, members__status=EventMember.Status.DECLINED)

    # One-off events + materialized exceptions still relevant today:
    # - timed: started before tomorrow and not yet finished (end >= now, or
    #   no end and start >= now)
    # - all-day: the day range [start, end) covers today's label. A bare
    #   start (no end, or a zero-length range) is a one-day event.
    timed_q = Q(all_day=False, start__lt=end_of_today) & (
        Q(end__gte=now) | Q(end__isnull=True, start__gte=now)
    )
    all_day_q = Q(all_day=True, start__lt=next_label) & (
        Q(start__gte=day_label) | Q(end__gt=day_label)
    )
    one_off = list(
        Event.objects.filter(
            user_q,
            timed_q | all_day_q,
            is_cancelled=False,
            is_recurring=False,
        )
        .exclude(declined_q)
        .select_related("calendar")
        .distinct()
    )

    # Recurring masters that could have occurrences today
    masters = list(
        Event.objects.filter(
            user_q,
            Q(all_day=False, start__lt=end_of_today)
            | Q(all_day=True, start__lt=next_label),
            is_recurring=True,
            recurrence_parent__isnull=True,
            is_cancelled=False,
        )
        .exclude(declined_q)
        .filter(
            Q(recurrence_until__isnull=True)
            | Q(recurrence_until__gte=min(now, day_label))
        )
        .select_related("calendar")
        .distinct()
    )

    # Build set of exception keys so we skip occurrences that are
    # cancelled or already materialized (those are in one_off above).
    exception_keys = set()
    if masters:
        for parent_id, orig_start in Event.objects.filter(
            recurrence_parent_id__in=[m.uuid for m in masters],
            original_start__isnull=False,
        ).values_list("recurrence_parent_id", "original_start"):
            exception_keys.add((str(parent_id), orig_start.isoformat()))

    # Expand virtual occurrences
    virtual = []
    for master in masters:
        window = (day_label, next_label) if master.all_day else (now, end_of_today)
        for occ_start in occurrences_in_range(master, *window):
            key = (str(master.uuid), occ_start.isoformat())
            if key not in exception_keys:
                virtual.append(VirtualOccurrence(master, occ_start))

    all_events = one_off + virtual
    all_events.sort(key=lambda e: (not e.all_day, e.start))
    return all_events
