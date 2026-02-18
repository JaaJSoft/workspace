"""User presence tracking service.

Uses Django cache as the fast path and syncs to DB periodically.
Thresholds:
  - online:  last activity < 2 min ago
  - away:    last activity < 10 min ago
  - offline: last activity >= 10 min ago

Cache keys:
  - ``presence:{user_id}``           — public ISO timestamp, TTL 600 s
  - ``presence:activity:{user_id}``  — real activity ISO timestamp (internal), TTL 600 s
  - ``presence:dbsync:{user_id}``    — throttle flag, TTL 30 s
  - ``presence:manual:{user_id}``    — manual status override, TTL 600 s
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


def _activity_key(user_id: int) -> str:
    return f'presence:activity:{user_id}'


def _dbsync_key(user_id: int) -> str:
    return f'presence:dbsync:{user_id}'


def _manual_key(user_id: int) -> str:
    return f'presence:manual:{user_id}'


# Statuses that override automatic detection ('auto' and 'online' do not)
MANUAL_OVERRIDES = {'away', 'busy', 'invisible'}
VALID_MANUAL_STATUSES = {'auto', 'online', 'away', 'busy', 'invisible'}


def set_manual_status(user_id: int, status: str) -> None:
    """Set a manual presence status for *user_id*."""
    from workspace.users.models import UserPresence

    cache.set(_manual_key(user_id), status, CACHE_TTL)
    UserPresence.objects.filter(user_id=user_id).update(manual_status=status)


def get_manual_status(user_id: int) -> str:
    """Return the manual status for *user_id* (from cache, fallback to DB)."""
    cached = cache.get(_manual_key(user_id))
    if cached is not None:
        return cached
    from workspace.users.models import UserPresence

    try:
        ms = UserPresence.objects.values_list('manual_status', flat=True).get(user_id=user_id)
    except UserPresence.DoesNotExist:
        ms = 'auto'
    cache.set(_manual_key(user_id), ms, CACHE_TTL)
    return ms


def touch(user_id: int) -> None:
    """Record activity for *user_id* (called by middleware on every request)."""
    now = timezone.now()
    iso = now.isoformat()

    # Always track real activity (internal only, never exposed)
    cache.set(_activity_key(user_id), iso, CACHE_TTL)

    # Public last_seen: skip update when user forces away/invisible
    update_public = get_manual_status(user_id) not in ('invisible', 'away')
    if update_public:
        cache.set(_cache_key(user_id), iso, CACHE_TTL)

    # Throttled DB sync — at most once per DB_SYNC_TTL seconds
    if cache.get(_dbsync_key(user_id)) is None:
        cache.set(_dbsync_key(user_id), '1', DB_SYNC_TTL)
        _sync_db(user_id, now, update_public=update_public)


def _sync_db(user_id: int, now: datetime, *, update_public: bool = True) -> None:
    from workspace.users.models import UserPresence

    defaults = {'last_activity': now}
    if update_public:
        defaults['last_seen'] = now
    UserPresence.objects.update_or_create(
        user_id=user_id,
        defaults=defaults,
    )


def get_status(user_id: int) -> str:
    """Return ``"online"``, ``"away"``, ``"busy"`` or ``"offline"`` for a single user."""
    manual = get_manual_status(user_id)
    if manual in MANUAL_OVERRIDES:
        # invisible appears as 'offline' to others
        return 'offline' if manual == 'invisible' else manual

    # 'auto' and 'online' — use automatic detection
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

    # Bulk-load manual statuses from cache
    manual_keys = {_manual_key(uid): uid for uid in user_ids}
    manual_cached = cache.get_many(list(manual_keys.keys()))
    manual_map: dict[int, str] = {}
    missing_manual: list[int] = []
    for key, uid in manual_keys.items():
        val = manual_cached.get(key)
        if val is not None:
            manual_map[uid] = val
        else:
            missing_manual.append(uid)

    # DB fallback for missing manual statuses
    if missing_manual:
        from workspace.users.models import UserPresence

        db_vals = dict(
            UserPresence.objects.filter(user_id__in=missing_manual)
            .values_list('user_id', 'manual_status')
        )
        to_cache = {}
        for uid in missing_manual:
            ms = db_vals.get(uid, 'auto')
            manual_map[uid] = ms
            to_cache[_manual_key(uid)] = ms
        if to_cache:
            cache.set_many(to_cache, CACHE_TTL)

    # Bulk-load activity timestamps
    keys = {_cache_key(uid): uid for uid in user_ids}
    cached = cache.get_many(list(keys.keys()))
    now = timezone.now()
    result = {}
    for key, uid in keys.items():
        ms = manual_map.get(uid, 'auto')
        if ms in MANUAL_OVERRIDES:
            result[uid] = 'offline' if ms == 'invisible' else ms
            continue

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
    """Return user IDs that should appear in presence lists.

    Includes: auto-detected active users + busy/away manual users.
    Excludes: invisible users (they appear offline to others).
    Single query using Q objects.
    """
    from django.db.models import Q
    from workspace.users.models import UserPresence

    cutoff = timezone.now() - AWAY_THRESHOLD
    return list(
        UserPresence.objects.filter(
            Q(last_seen__gte=cutoff, manual_status__in=('auto', 'online', 'busy', 'away'))
            | Q(manual_status__in=('busy', 'away'))
        )
        .values_list('user_id', flat=True)
    )


def clear(user_id: int) -> None:
    """Remove presence data from cache so the user appears offline immediately."""
    cache.delete(_cache_key(user_id))
    cache.delete(_activity_key(user_id))
    cache.delete(_dbsync_key(user_id))
    cache.delete(_manual_key(user_id))


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
