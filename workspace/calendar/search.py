import unicodedata

from workspace.core.module_registry import SearchResult, SearchTag

from .models import Poll
from .services.event_search import search_events_qs


def _fold(text):
    """Lowercase and strip diacritics, mirroring the FTS `remove_diacritics`
    tokenizer closely enough to locate which field a hit came from."""
    stripped = "".join(
        c
        for c in unicodedata.normalize("NFKD", text or "")
        if not unicodedata.combining(c)
    )
    return stripped.casefold()


def _event_match_display(event, query):
    """Pick the field a full-text hit most likely matched, for display.

    Events are indexed across title, description and location, but a result
    row shows one snippet. Prefer the title (title hits keep their existing
    display), then location, then a description excerpt, falling back to the
    title so a hit never renders an empty or misleading snippet.
    """
    folded = _fold(query)
    if folded and folded in _fold(event.title):
        return event.title, "title"
    if folded and event.location and folded in _fold(event.location):
        return event.location, "location"
    if folded and event.description and folded in _fold(event.description):
        return event.description[:120], "description"
    return event.title, "title"


def search_events(query, user, limit):
    events = search_events_qs(user, query).select_related("calendar")[:limit]

    results = []
    for e in events:
        matched_value, match_type = _event_match_display(e, query)
        results.append(
            SearchResult(
                uuid=str(e.uuid),
                name=e.title,
                url=f"/calendar?event={e.uuid}",
                matched_value=matched_value,
                match_type=match_type,
                type_icon="calendar",
                module_slug="calendar",
                module_color="accent",
                tags=(SearchTag(e.calendar.name, "accent"),) if e.calendar else (),
            )
        )
    return results


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
