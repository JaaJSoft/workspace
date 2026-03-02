"""Service layer for activity feed operations.

Shared between the REST API views and the dashboard template views.
"""

from django.utils import timezone

from workspace.core.activity_registry import activity_registry


def get_sources():
    """Return list of available activity sources with metadata."""
    return [
        {'slug': info.slug, 'label': info.label, 'icon': info.icon, 'color': info.color}
        for info in activity_registry.get_all().values()
    ]


def get_recent_events(
    *,
    user_id=None,
    viewer_id=None,
    source=None,
    exclude_user_id=None,
    limit=10,
    offset=0,
):
    """Fetch recent activity events with optional filtering.

    Args:
        user_id: Whose activity to fetch (None = all users).
        viewer_id: Who is viewing (for access filtering).
        source: Provider slug to filter by (None = all).
        exclude_user_id: Exclude events from this user (e.g. self).
        limit: Max events to return.
        offset: Skip this many events.
    """
    if exclude_user_id is not None:
        # Fetch extra to account for post-filter exclusion
        fetch_limit = limit + 20
    else:
        fetch_limit = limit

    events = activity_registry.get_recent_events(
        user_id,
        limit=fetch_limit,
        offset=offset if exclude_user_id is None else 0,
        viewer_id=viewer_id,
        source=source,
    )

    if exclude_user_id is not None:
        events = [e for e in events if e.get('actor', {}).get('id') != exclude_user_id]
        events = events[offset:offset + limit]
    return events


def annotate_time_ago(events):
    """Add a human-readable 'time_ago' field to each event dict in place."""
    now = timezone.now()
    for event in events:
        ts = event.get('timestamp')
        if not ts:
            event['time_ago'] = ''
            continue
        diff = (now - ts).total_seconds()
        if diff < 60:
            event['time_ago'] = 'now'
        elif diff < 3600:
            event['time_ago'] = f'{int(diff // 60)}m'
        elif diff < 86400:
            event['time_ago'] = f'{int(diff // 3600)}h'
        elif diff < 604800:
            event['time_ago'] = f'{int(diff // 86400)}d'
        else:
            event['time_ago'] = ts.strftime('%b %d')
    return events


def serialize_timestamps(events):
    """Convert datetime timestamps to ISO strings for JSON API responses."""
    for event in events:
        if hasattr(event.get('timestamp'), 'isoformat'):
            event['timestamp'] = event['timestamp'].isoformat()
    return events
