from workspace.common.search import apply_fulltext
from workspace.common.search.schema import Col, FulltextIndex

from ..models import Event
from ..queries import visible_events_q

EVENT_FTS = FulltextIndex(
    table="calendar_event",
    columns=(
        Col("title"),
        Col("description", "C", cap=100_000),
        Col("location", "B"),
    ),
)


def fts_events(qs, query):
    """Filter qs to full-text matches, annotated with `search_rank`.

    Caller applies order_by.
    """
    return apply_fulltext(qs, query, index=EVENT_FTS)


def search_events_qs(user, query):
    """Ranked event search over title/description/location for `user`.

    Master events only (recurrence exceptions carry copies of the parent's
    text) and no cancelled events - same visibility rules as the existing
    search surfaces.
    """
    qs = Event.objects.filter(
        visible_events_q(user),
        recurrence_parent__isnull=True,
        is_cancelled=False,
    ).distinct()
    return fts_events(qs, query).order_by("-search_rank", "-start")
