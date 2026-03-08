from workspace.core.module_registry import SearchResult, SearchTag
from .models import Event, Poll
from .queries import visible_events_q


def search_events(query, user, limit):
    events = (
        Event.objects
        .select_related('calendar')
        .filter(
            visible_events_q(user),
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
