from django.db.models import Q

from workspace.core.module_registry import SearchResult, SearchTag
from .models import Calendar, CalendarSubscription, Event, EventMember, Poll


def search_events(query, user, limit):
    owned_cal_ids = Calendar.objects.filter(owner=user).values_list('uuid', flat=True)
    sub_cal_ids = CalendarSubscription.objects.filter(user=user).values_list('calendar_id', flat=True)
    member_event_ids = EventMember.objects.filter(user=user).values_list('event_id', flat=True)

    events = (
        Event.objects
        .select_related('calendar')
        .filter(
            Q(calendar_id__in=owned_cal_ids) |
            Q(calendar_id__in=sub_cal_ids) |
            Q(uuid__in=member_event_ids),
            title__icontains=query,
            recurrence_parent__isnull=True,
            is_cancelled=False,
        )
        .distinct()
        .order_by('-start')[:limit]
    )

    return [
        SearchResult(
            uuid=str(e.uuid),
            name=e.title,
            url=f'/calendar?event={e.uuid}',
            matched_value=e.title,
            match_type='title',
            type_icon='calendar',
            module_slug='calendar',
            module_color='accent',
            tags=(SearchTag(e.calendar.name, 'accent'),) if e.calendar else (),
        )
        for e in events
    ]


def search_polls(query, user, limit):
    polls = (
        Poll.objects
        .filter(created_by=user, title__icontains=query)
        .order_by('-created_at')[:limit]
    )

    return [
        SearchResult(
            uuid=str(p.uuid),
            name=p.title,
            url=f'/calendar?poll={p.uuid}',
            matched_value=p.title,
            match_type='title',
            type_icon='bar-chart-3',
            module_slug='calendar',
            module_color='accent',
            tags=(SearchTag(p.status.capitalize(), 'accent'),),
        )
        for p in polls
    ]
