"""User presence tracking service.

Uses Django cache as the fast path and syncs to DB periodically.
Thresholds:
  - online:  last activity < 2 min ago
  - away:    last activity < 10 min ago
  - offline: last activity >= 10 min ago

Cache keys:
  - ``presence:{user_id}``           — ISO timestamp, TTL 600 s
  - ``presence:dbsync:{user_id}``    — throttle flag, TTL 30 s
"""

import logging
from datetime import datetime, timedelta

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

ONLINE_THRESHOLD = timedelta(minutes=2)
AWAY_THRESHOLD = timedelta(minutes=10)
CACHE_TTL = 600  # seconds
DB_SYNC_TTL = 30  # seconds


def _cache_key(user_id: int) -> str:
    return f'presence:{user_id}'


def _dbsync_key(user_id: int) -> str:
    return f'presence:dbsync:{user_id}'


def touch(user_id: int) -> None:
    """Record activity for *user_id* (called by middleware on every request)."""
    now = timezone.now()
    iso = now.isoformat()
    cache.set(_cache_key(user_id), iso, CACHE_TTL)

    # Throttled DB sync — at most once per DB_SYNC_TTL seconds
    if cache.get(_dbsync_key(user_id)) is None:
        cache.set(_dbsync_key(user_id), '1', DB_SYNC_TTL)
        _sync_db(user_id, now)


def _sync_db(user_id: int, now: datetime) -> None:
    from workspace.users.models import UserPresence

    UserPresence.objects.update_or_create(
        user_id=user_id,
        defaults={'last_seen': now},
    )


def get_status(user_id: int) -> str:
    """Return ``"online"``, ``"away"`` or ``"offline"`` for a single user."""
    raw = cache.get(_cache_key(user_id))
    if raw is None:
        return 'offline'
    try:
        last = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return 'offline'
    delta = timezone.now() - last
    if delta < ONLINE_THRESHOLD:
        return 'online'
    if delta < AWAY_THRESHOLD:
        return 'away'
    return 'offline'


def get_statuses(user_ids: list[int]) -> dict[int, str]:
    """Bulk status lookup via ``cache.get_many``."""
    if not user_ids:
        return {}
    keys = {_cache_key(uid): uid for uid in user_ids}
    cached = cache.get_many(list(keys.keys()))
    now = timezone.now()
    result = {}
    for key, uid in keys.items():
        raw = cached.get(key)
        if raw is None:
            result[uid] = 'offline'
            continue
        try:
            delta = now - datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            result[uid] = 'offline'
            continue
        if delta < ONLINE_THRESHOLD:
            result[uid] = 'online'
        elif delta < AWAY_THRESHOLD:
            result[uid] = 'away'
        else:
            result[uid] = 'offline'
    return result


def get_online_user_ids() -> list[int]:
    """Return user IDs seen within the away threshold (online + away)."""
    from workspace.users.models import UserPresence

    cutoff = timezone.now() - AWAY_THRESHOLD
    return list(
        UserPresence.objects.filter(last_seen__gte=cutoff)
        .values_list('user_id', flat=True)
    )


def get_last_seen(user_id: int) -> datetime | None:
    """Return the last-seen datetime, from cache first then DB fallback."""
    raw = cache.get(_cache_key(user_id))
    if raw is not None:
        try:
            return datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            pass
    from workspace.users.models import UserPresence

    try:
        return UserPresence.objects.values_list('last_seen', flat=True).get(user_id=user_id)
    except UserPresence.DoesNotExist:
        return None
