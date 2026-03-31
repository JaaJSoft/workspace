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

    # One-off events + materialized exceptions with start today
    one_off = list(
        Event.objects.filter(
            user_q,
            start__gte=now,
            start__lte=end_of_today,
            is_cancelled=False,
            recurrence_frequency__isnull=True,
        )
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
