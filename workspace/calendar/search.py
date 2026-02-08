from django.db.models import Q

from workspace.core.module_registry import SearchResult
from .models import Calendar, CalendarSubscription, Event, EventMember


def search_events(query, user, limit):
    owned_cal_ids = Calendar.objects.filter(owner=user).values_list('uuid', flat=True)
    sub_cal_ids = CalendarSubscription.objects.filter(user=user).values_list('calendar_id', flat=True)
    member_event_ids = EventMember.objects.filter(user=user).values_list('event_id', flat=True)

    events = (
        Event.objects.filter(
            Q(calendar_id__in=owned_cal_ids) |
            Q(calendar_id__in=sub_cal_ids) |
            Q(uuid__in=member_event_ids),
            title__icontains=query,
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
        )
        for e in events
    ]
