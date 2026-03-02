import time
import logging
import threading

from django.db.models import Q
from django.utils import timezone

from workspace.core.sse_registry import SSEProvider
from workspace.users import presence_service

logger = logging.getLogger(__name__)

# Process-level cache for presence snapshots shared across all SSE connections.
_snapshot_lock = threading.Lock()
_cached_snapshot = None
_cached_snapshot_ts = 0
_SNAPSHOT_TTL = 5  # seconds


def _build_global_snapshot():
    """Build presence snapshot, cached across all connections for _SNAPSHOT_TTL seconds."""
    global _cached_snapshot, _cached_snapshot_ts

    now = time.monotonic()
    if _cached_snapshot is not None and now - _cached_snapshot_ts < _SNAPSHOT_TTL:
        return _cached_snapshot

    with _snapshot_lock:
        # Double-check after acquiring lock
        if _cached_snapshot is not None and time.monotonic() - _cached_snapshot_ts < _SNAPSHOT_TTL:
            return _cached_snapshot

        snapshot = _query_presence_snapshot()
        _cached_snapshot = snapshot
        _cached_snapshot_ts = time.monotonic()
        return snapshot


def _query_presence_snapshot():
    """Execute the actual DB query for presence data."""
    from workspace.users.models import UserPresence

    now = timezone.now()
    cutoff = now - presence_service.AWAY_THRESHOLD
    online_cutoff = now - presence_service.ONLINE_THRESHOLD

    rows = list(
        UserPresence.objects.filter(
            Q(last_seen__gte=cutoff) & ~Q(manual_status='invisible')
            | Q(manual_status__in=('busy', 'away'))
        ).values_list('user_id', 'last_seen', 'manual_status')
    )

    online, away, busy = [], [], []
    for uid, last_seen, manual in rows:
        if manual == 'invisible':
            continue
        if manual == 'busy':
            busy.append(uid)
        elif manual == 'away':
            away.append(uid)
        elif manual in ('auto', 'online'):
            if last_seen >= online_cutoff:
                online.append(uid)
            elif last_seen >= cutoff:
                away.append(uid)

    bot_ids = list(PresenceSSEProvider._get_bot_ids())

    return {'online': online, 'away': away, 'busy': busy, 'bot': bot_ids}


class PresenceSSEProvider(SSEProvider):
    """Broadcasts presence snapshots (online/away user lists) via SSE."""

    def __init__(self, user, last_event_id):
        super().__init__(user, last_event_id)
        self._last_snapshot = None
        self._last_push = 0

    @staticmethod
    def _get_bot_ids():
        """Return bot user IDs (cached for the process lifetime of the snapshot cycle)."""
        from django.core.cache import cache

        cache_key = 'presence:bot_user_ids'
        bot_ids = cache.get(cache_key)
        if bot_ids is None:
            from workspace.ai.models import BotProfile
            bot_ids = list(BotProfile.objects.values_list('user_id', flat=True))
            cache.set(cache_key, bot_ids, 300)  # 5 min TTL
        return bot_ids

    def get_initial_events(self):
        snapshot = _build_global_snapshot()
        self._last_snapshot = snapshot
        self._last_push = time.time()
        return [('presence_snapshot', snapshot, None)]

    def poll(self, cache_value):
        now = time.time()
        # Rebuild every ~10 seconds regardless of dirty flag
        if now - self._last_push < 10:
            return []
        self._last_push = now
        snapshot = _build_global_snapshot()
        # Only emit if the snapshot changed
        if snapshot == self._last_snapshot:
            return []
        self._last_snapshot = snapshot
        return [('presence_snapshot', snapshot, None)]
