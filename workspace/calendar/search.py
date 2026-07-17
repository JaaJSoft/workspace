from workspace.core.module_registry import SearchResult, SearchTag

from .models import Poll
from .services.event_search import search_events_qs


def search_events(query, user, limit):
    events = search_events_qs(user, query).select_related("calendar")[:limit]

    return [
        SearchResult(
            uuid=str(e.uuid),
            name=e.title,
            url=f"/calendar?event={e.uuid}",
            matched_value=e.title,
            match_type="title",
            type_icon="calendar",
            module_slug="calendar",
            module_color="accent",
            tags=(SearchTag(e.calendar.name, "accent"),) if e.calendar else (),
        )
        for e in events
    ]


def search_polls(query, user, limit):
    polls = Poll.objects.filter(created_by=user, title__icontains=query).order_by(
        "-created_at"
    )[:limit]

    return [
        SearchResult(
            uuid=str(p.uuid),
            name=p.title,
            url=f"/calendar?poll={p.uuid}",
            matched_value=p.title,
            match_type="title",
            type_icon="bar-chart-3",
            module_slug="calendar",
            module_color="accent",
            tags=(SearchTag(p.status.capitalize(), "accent"),),
        )
        for p in polls
    ]
