"""Upcoming events helper that expands recurring occurrences.

Used by the dashboard (event list + pending action count).
"""

from django.db.models import Q

from workspace.calendar.models import Event, EventMember
from workspace.calendar.queries import visible_events_q
from workspace.calendar.recurrence import _build_rrule


class VirtualOccurrence:
    """Lightweight event-like object for recurring occurrences in templates."""

    __slots__ = ('uuid', 'title', 'all_day', 'start', 'calendar')

    def __init__(self, master, occ_start):
        self.uuid = f'{master.uuid}:{occ_start.isoformat()}'
        self.title = master.title
        self.all_day = master.all_day
        self.start = occ_start
        self.calendar = master.calendar


def get_upcoming_for_user(user, now, end_of_today):
    """Return today's upcoming events including recurring occurrences.

    Returns a sorted list mixing real Event instances and VirtualOccurrence
    objects (both expose .uuid, .title, .all_day, .start, .calendar).
    """
    user_q = visible_events_q(user)
    # Exclude events the user explicitly declined
    declined_q = Q(members__user=user, members__status=EventMember.Status.DECLINED)

    # One-off events + materialized exceptions with start today
    one_off = list(
        Event.objects.filter(
            user_q,
            start__gte=now,
            start__lte=end_of_today,
            is_cancelled=False,
            recurrence_frequency__isnull=True,
        )
        .exclude(declined_q)
        .select_related('calendar')
        .distinct()
    )

    # Recurring masters that could have occurrences today
    masters = list(
        Event.objects.filter(
            user_q,
            recurrence_frequency__isnull=False,
            recurrence_parent__isnull=True,
            start__lte=end_of_today,
            is_cancelled=False,
        )
        .exclude(declined_q)
        .filter(Q(recurrence_end__isnull=True) | Q(recurrence_end__gte=now))
        .select_related('calendar')
        .distinct()
    )

    if not masters:
        one_off.sort(key=lambda e: e.start)
        return one_off

    # Build set of exception keys so we skip occurrences that are
    # cancelled or already materialized (those are in one_off above).
    master_ids = [m.uuid for m in masters]
    exception_keys = set()
    for parent_id, orig_start in (
        Event.objects.filter(
            recurrence_parent_id__in=master_ids,
            original_start__isnull=False,
        ).values_list('recurrence_parent_id', 'original_start')
    ):
        exception_keys.add((str(parent_id), orig_start.isoformat()))

    # Expand virtual occurrences
    virtual = []
    for master in masters:
        for occ_start in _build_rrule(master, now, end_of_today):
            key = (str(master.uuid), occ_start.isoformat())
            if key not in exception_keys:
                virtual.append(VirtualOccurrence(master, occ_start))

    all_events = one_off + virtual
    all_events.sort(key=lambda e: e.start)
    return all_events


def get_upcoming_page(user, after, limit, calendar_ids=None, show_declined=False):
    """Cursor-paginated variant of get_upcoming_for_user.

    Returns (events_dicts, next_after) where `next_after` is an ISO string or
    None if there are no more events.
    """
    from workspace.calendar.serializers import EventSerializer
    from workspace.calendar.recurrence import (
        next_occurrences_after, make_virtual_occurrence, make_exception_dict,
    )

    user_q = visible_events_q(user)
    declined_q = Q(members__user=user, members__status=EventMember.Status.DECLINED)

    # ---- One-off events + materialized exceptions ----
    one_off_qs = (
        Event.objects.filter(
            user_q,
            start__gte=after,
            is_cancelled=False,
            recurrence_frequency__isnull=True,
        )
        .select_related('owner', 'calendar', 'recurrence_parent')
        .prefetch_related('members__user')
        .distinct()
    )
    if calendar_ids is not None:
        one_off_qs = one_off_qs.filter(calendar_id__in=calendar_ids)
    if not show_declined:
        one_off_qs = one_off_qs.exclude(declined_q)

    # +1 sentinel so we can tell if there are more events after this page.
    one_off = list(one_off_qs.order_by('start')[:limit + 1])
    one_off_data = EventSerializer(one_off, many=True).data

    # ---- Recurring masters ----
    masters_qs = (
        Event.objects.filter(
            user_q,
            recurrence_frequency__isnull=False,
            recurrence_parent__isnull=True,
            is_cancelled=False,
        )
        # Master can still produce occurrences at or after `after`
        .filter(Q(recurrence_end__isnull=True) | Q(recurrence_end__gte=after))
        .select_related('owner', 'calendar')
        .prefetch_related('members__user')
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
        for parent_id, orig_start in (
            Event.objects.filter(
                recurrence_parent_id__in=master_ids,
                original_start__isnull=False,
            ).values_list('recurrence_parent_id', 'original_start')
        ):
            exception_keys.add((str(parent_id), orig_start.isoformat()))

    from workspace.calendar.recurrence import _member_dict

    recurring_data = []
    for master in masters:
        # Pre-build member dicts once per master, then `make_virtual_occurrence`
        # reuses them via `getattr(master, '_cached_member_dicts', ...)`. Same
        # pattern as `expand_recurring_events`.
        master._cached_member_dicts = [_member_dict(m) for m in master.members.all()]

        # Iterate the rrule stream unbounded and collect up to `limit + 1`
        # NON-excepted occurrences. We can't just request `limit + 1` from
        # `next_occurrences_after` because exceptions in the stream would
        # silently truncate the cursor and drop `next_after` to None even
        # though the series continues.
        collected = 0
        for occ_start in next_occurrences_after(master, after):
            if collected > limit:
                break
            key = (str(master.uuid), occ_start.isoformat())
            if key in exception_keys:
                continue  # exception or cancellation handled separately
            recurring_data.append(make_virtual_occurrence(master, occ_start))
            collected += 1

    # ---- Merge, sort, slice ----
    # Sort by parsed datetime so that DRF's Z-suffix UTC strings
    # ("2026-04-08T14:00:00Z") and plain isoformat strings from virtual
    # occurrences ("2026-04-08T14:00:00+00:00") compare correctly at the
    # same instant. String comparison would be wrong.
    from dateutil.parser import parse as _parse_dt

    merged = one_off_data + recurring_data
    merged.sort(key=lambda e: (_parse_dt(e['start']), e['uuid']))

    page = merged[:limit]
    next_after = merged[limit]['start'] if len(merged) > limit else None
    return page, next_after
