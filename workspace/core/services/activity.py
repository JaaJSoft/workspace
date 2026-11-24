"""Service layer for activity feed operations.

Shared between the REST API views and the dashboard template views.
"""

from django.utils import timezone

from workspace.common.cache import cached
from workspace.common.dates import time_ago
from workspace.core.activity_registry import activity_registry, module_color

from .module_visibility import hidden_module_slugs, visible_modules

# Usage stats are aggregate counts (file totals, message counts, ...) that
# change slowly and are purely informational. The dashboard and profile views
# read them on every page load, where they otherwise trigger a query fan-out
# across every activity provider. A short time-based cache removes that cost;
# worst-case staleness is the TTL.
_USAGE_STATS_TTL = 60  # seconds
# How far back a feed pages. Every provider reads offset + limit rows to merge
# them, so the offset is what a request costs, and it is the caller's to pick.
MAX_ACTIVITY_OFFSET = 1000


def get_sources(user=None):
    """Return list of available activity sources with metadata.

    With *user*, sources whose module is hidden from that user (preview
    modules restricted by ``PREVIEW_VISIBILITY``) are omitted - UI callers
    pass the viewing user so the tabs match the nav. Without it, the full
    list is returned.
    """
    infos = activity_registry.get_all().values()
    if user is not None:
        hidden = hidden_module_slugs(user)
        infos = [info for info in infos if info.slug not in hidden]
    return [
        {
            "slug": info.slug,
            "label": info.label,
            "icon": info.icon,
            "color": module_color(info.slug),
        }
        for info in infos
    ]


def allowed_activity_sources(user):
    """Slugs of the activity sources whose module *user* may see."""
    return set(activity_registry.get_all()) - hidden_module_slugs(user)


@cached(
    key=lambda user_id, viewer_id=None: f"activity:stats:{user_id}:{viewer_id}",
    ttl=_USAGE_STATS_TTL,
)
def get_usage_stats(user_id, viewer_id=None):
    """Return per-module usage stats for *user_id*, cached for a short TTL.

    Thin wrapper over ``activity_registry.get_stats`` that the dashboard and
    profile views call on the hot path. Caching lives here, at the service
    layer, rather than inside the registry so the registry stays pure and
    deterministic for its own unit tests.
    """
    return activity_registry.get_stats(user_id, viewer_id=viewer_id)


def get_visible_usage_stats(user_id, *, viewer_id=None, visible_to):
    """``get_usage_stats`` without the modules hidden from *visible_to*."""
    allowed = allowed_activity_sources(visible_to)
    stats = get_usage_stats(user_id, viewer_id=viewer_id)
    return {slug: value for slug, value in stats.items() if slug in allowed}


def get_usage_headlines(user):
    """The usage popover's rows for *user*, in module order.

    One row per visible module whose provider publishes a headline, carrying
    the module's link, icon and color.
    """
    stats = get_visible_usage_stats(user.id, visible_to=user)
    rows = []
    for module in visible_modules(user):
        headline = activity_registry.get_headline(module.slug, stats.get(module.slug))
        if headline is None:
            continue
        rows.append(
            {
                "slug": module.slug,
                "url": module.url,
                "icon": module.icon,
                "color": module.color,
                "count": headline.count,
                "label": headline.label,
                "detail": headline.detail,
            }
        )
    return rows


def _daily_counts_key(
    user_id, date_from, date_to, viewer_id=None, allowed_sources=None
):
    sources = "*" if allowed_sources is None else ",".join(sorted(allowed_sources))
    return f"activity:daily:{user_id}:{viewer_id}:{date_from}:{date_to}:{sources}"


@cached(key=_daily_counts_key, ttl=_USAGE_STATS_TTL)
def get_daily_counts(user_id, date_from, date_to, viewer_id=None, allowed_sources=None):
    """Return per-day activity counts for *user_id*, cached for a short TTL.

    Backs the profile heatmap, where the registry fans out one 12-month
    aggregate query per provider on every render. Same rationale and TTL as
    ``get_usage_stats``: purely informational, worst-case staleness is the
    TTL. The dates are part of the key, so the daily window shift naturally
    misses the previous day's entry. The counts are summed across providers
    and cannot be narrowed afterwards, so *allowed_sources* is part of the
    key too.
    """
    return activity_registry.get_daily_counts(
        user_id,
        date_from,
        date_to,
        viewer_id=viewer_id,
        allowed_sources=allowed_sources,
    )


# How many events a search reads before giving up on filling its page.
SEARCH_SCAN_LIMIT = 5000
SEARCH_BATCH = 200


def get_recent_events(
    *,
    user_id=None,
    viewer_id=None,
    source=None,
    exclude_user_id=None,
    search=None,
    limit=10,
    offset=0,
    visible_to=None,
):
    """Fetch recent activity events with optional filtering.

    Args:
        user_id: Whose activity to fetch (None = all users).
        viewer_id: Who is viewing (for access filtering).
        source: Provider slug to filter by (None = all).
        exclude_user_id: Exclude events from this user (e.g. self).
        search: Text to filter events by (matches description, label, actor).
        limit: Max events to return.
        offset: Skip this many events; past ``MAX_ACTIVITY_OFFSET`` the feed
            is empty.
        visible_to: User whose module visibility gates the sources - UI
            callers pass the viewing user so preview modules hidden from
            them contribute no events (and an explicitly requested hidden
            source yields an empty feed).
    """
    if offset > MAX_ACTIVITY_OFFSET:
        return []
    allowed_sources = None
    if visible_to is not None:
        allowed = allowed_activity_sources(visible_to)
        if source is not None:
            if source not in allowed:
                return []
        else:
            allowed_sources = allowed

    def fetch(count, skip):
        return activity_registry.get_recent_events(
            user_id,
            limit=count,
            offset=skip,
            viewer_id=viewer_id,
            source=source,
            exclude_actor_id=exclude_user_id,
            allowed_sources=allowed_sources,
        )

    if not search:
        return fetch(limit, offset)

    # Events are built by each module in Python, so a search filters them
    # here. It reads further back until the page is full or the feed runs
    # out, each round twice as deep, up to SEARCH_SCAN_LIMIT events.
    query = search.lower()
    wanted = offset + limit
    depth = max(wanted * 2, SEARCH_BATCH)
    while True:
        events = fetch(depth, 0)
        matches = [e for e in events if _matches(e, query)]
        if len(matches) >= wanted or len(events) < depth or depth >= SEARCH_SCAN_LIMIT:
            return matches[offset:wanted]
        depth = min(depth * 2, SEARCH_SCAN_LIMIT)


def _matches(event, query):
    actor = event.get("actor")
    username = actor.get("username", "") if isinstance(actor, dict) else ""
    return any(
        query in (text or "").lower()
        for text in (event.get("description", ""), event.get("label", ""), username)
    )


def annotate_time_ago(events):
    """Add a human-readable 'time_ago' field to each event dict in place."""
    now = timezone.now()
    for event in events:
        event["time_ago"] = time_ago(event.get("timestamp"), now=now)
    return events


def serialize_timestamps(events):
    """Convert datetime timestamps to ISO strings for JSON API responses."""
    for event in events:
        if hasattr(event.get("timestamp"), "isoformat"):
            event["timestamp"] = event["timestamp"].isoformat()
    return events
