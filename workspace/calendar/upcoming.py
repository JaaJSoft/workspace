"""Upcoming events helper that expands recurring occurrences.

Used by the dashboard event list and the today's-events notification cron.
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import NamedTuple

from dateutil.parser import parse as _parse_dt
from django.db.models import Q
from django.utils import timezone

from workspace.calendar.models import Event, EventMember
from workspace.calendar.queries import visible_events_q
from workspace.calendar.recurrence import (
    _member_dict,
    make_virtual_occurrence,
    next_occurrences_after,
    occurrences_in_range,
)
from workspace.calendar.services.recurrence_rule import describe, to_simple_json
from workspace.calendar.services.timezones import all_day_label
from workspace.common.datetimes import local_date_range


def event_instant(value):
    """Return the aware instant a serialized event ``start``/``end`` stands for.

    Timed events serialize as aware ISO strings, in either the DRF Z-suffix
    form or the ``+00:00`` isoformat of a virtual occurrence. An all-day
    event serializes as the bare UTC date of its day label, which parses
    naive; UTC midnight is the instant that date stands for.
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


def _agenda_key(item, tz):
    """Position of a serialized event in the agenda's order.

    A timed event sits at its start; an all-day event at the start of its
    day in *tz*, ahead of that day's timed events. Ordering all-day rows by
    their UTC-midnight storage instead would slot a west-of-UTC user's
    Tuesday all-day event among their Monday evening events.
    """
    if item["all_day"]:
        return datetime.combine(date.fromisoformat(item["start"]), time.min, tzinfo=tz)
    return event_instant(item["start"])


class AgendaCursor(NamedTuple):
    """Where the next agenda page starts: the first event it holds.

    ``after`` is that event's ``_agenda_key``, ``event_id`` its serialized
    uuid. Both are needed: every all-day event of a day shares one key, so
    a cursor without the id would serve the same rows again once more of
    them than a page holds fall on one day.
    """

    after: datetime
    event_id: str


def _tie_q(event_id):
    """Rows sharing the cursor's key that sort at or after *event_id*.

    Rows tie-break on their uuid string; a recurring occurrence's id is
    ``<master uuid>:<start>``, which sorts just past its master's uuid.
    """
    head, separator, _ = event_id.partition(":")
    return Q(uuid__gt=head) if separator else Q(uuid__gte=head)


def get_upcoming_page(
    user, after, limit, calendar_ids=None, show_declined=False, tz=None, after_id=None
):
    """Cursor-paginated agenda, in agenda order (see ``_agenda_key``).

    The first page (no *after_id*) holds the timed events starting at or
    after *after* and the all-day events of the local day of *after* in
    *tz* (the active timezone by default) onwards, so today's all-day
    events stay listed all day long. A later page resumes at the cursor the
    previous one returned: *after* and *after_id* from its ``AgendaCursor``.

    Returns ``(events_dicts, next_cursor)``, ``next_cursor`` None at the end.
    """
    from workspace.calendar.serializers import EventSerializer

    if tz is None:
        tz = timezone.get_current_timezone()
    after_day = after.astimezone(tz).date()
    first_label = all_day_label(after_day)
    resume = None if after_id is None else (after, after_id)

    if resume is None:
        timed_q = Q(start__gte=after)
        all_day_q = Q(start__gte=first_label)
    else:
        tie_q = _tie_q(after_id)
        timed_q = Q(start__gt=after) | (Q(start=after) & tie_q)
        all_day_q = Q(start__gt=first_label)
        if datetime.combine(after_day, time.min, tzinfo=tz) == after:
            # The cursor sits at the start of that day: its all-day events
            # share the cursor's key.
            all_day_q |= Q(start=first_label) & tie_q

    user_q = visible_events_q(user)
    declined_q = Q(members__user=user, members__status=EventMember.Status.DECLINED)

    # ---- One-off events + materialized exceptions ----
    one_off_qs = (
        Event.objects.filter(
            user_q,
            is_cancelled=False,
            is_recurring=False,
        )
        .select_related("owner", "calendar", "recurrence_parent")
        .prefetch_related("members__user")
        .distinct()
    )
    if calendar_ids is not None:
        one_off_qs = one_off_qs.filter(calendar_id__in=calendar_ids)
    if not show_declined:
        one_off_qs = one_off_qs.exclude(declined_q)

    # Timed and all-day rows are read as two streams, each in agenda order
    # and each with a +1 sentinel: a single ORDER BY start would rank them
    # by storage instant, and its LIMIT could cut an all-day row that the
    # merge below places before a timed one it kept.
    one_off = [
        *one_off_qs.filter(timed_q, all_day=False).order_by("start", "uuid")[
            : limit + 1
        ],
        *one_off_qs.filter(all_day_q, all_day=True).order_by("start", "uuid")[
            : limit + 1
        ],
    ]
    one_off_data = EventSerializer(one_off, many=True).data

    # ---- Recurring masters ----
    masters_qs = (
        Event.objects.filter(
            user_q,
            is_recurring=True,
            recurrence_parent__isnull=True,
            is_cancelled=False,
        )
        # Master can still produce occurrences at or after `after`
        .filter(
            Q(recurrence_until__isnull=True)
            | Q(recurrence_until__gte=min(after, first_label))
        )
        .select_related("owner", "calendar")
        .prefetch_related("members__user")
        .distinct()
    )
    if calendar_ids is not None:
        masters_qs = masters_qs.filter(calendar_id__in=calendar_ids)
    if not show_declined:
        masters_qs = masters_qs.exclude(declined_q)

    masters = list(masters_qs)

    # Build exception key index so we skip occurrences that are either
    # materialized (already in one_off) or explicitly cancelled.
    master_ids = [m.uuid for m in masters]
    exception_keys = set()
    if master_ids:
        for parent_id, orig_start in Event.objects.filter(
            recurrence_parent_id__in=master_ids,
            original_start__isnull=False,
        ).values_list("recurrence_parent_id", "original_start"):
            exception_keys.add((str(parent_id), orig_start.isoformat()))

    recurring_data = []
    for master in masters:
        # Pre-build the per-master values once, then `make_virtual_occurrence`
        # reuses them via `getattr(master, '_cached_...', ...)`. Same pattern as
        # `expand_recurring_events`: members, plus the two derived recurrence
        # fields, which are pure functions of the rule text and would otherwise
        # be recomputed for every occurrence on the page.
        master._cached_member_dicts = [_member_dict(m) for m in master.members.all()]
        master._cached_recurrence_summary = describe(master.recurrence_rule)
        master._cached_recurrence_simple = to_simple_json(master.recurrence_rule)

        # Iterate the rrule stream unbounded and collect up to `limit + 1`
        # NON-excepted occurrences. We can't just request `limit + 1` from
        # `next_occurrences_after` because exceptions in the stream would
        # silently truncate the cursor and drop `next_after` to None even
        # though the series continues.
        collected = 0
        from_instant = first_label if master.all_day else after
        for occ_start in next_occurrences_after(master, from_instant):
            if collected > limit:
                break
            key = (str(master.uuid), occ_start.isoformat())
            if key in exception_keys:
                continue  # exception or cancellation handled separately
            occurrence = make_virtual_occurrence(master, occ_start)
            if resume and (_agenda_key(occurrence, tz), occurrence["uuid"]) < resume:
                continue  # served by an earlier page
            recurring_data.append(occurrence)
            collected += 1

    # ---- Merge, sort, slice ----
    # Sort by instant, not by string: the two start formats do not sort
    # lexically at the same instant.
    merged = one_off_data + recurring_data
    merged.sort(key=lambda e: (_agenda_key(e, tz), e["uuid"]))

    page = merged[:limit]
    if len(merged) <= limit:
        return page, None
    first_next = merged[limit]
    return page, AgendaCursor(_agenda_key(first_next, tz), first_next["uuid"])
