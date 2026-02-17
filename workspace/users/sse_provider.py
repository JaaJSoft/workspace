import time
import logging

from workspace.core.sse_registry import SSEProvider
from workspace.users import presence_service

logger = logging.getLogger(__name__)


class PresenceSSEProvider(SSEProvider):
    """Broadcasts presence snapshots (online/away user lists) via SSE."""

    def __init__(self, user, last_event_id):
        super().__init__(user, last_event_id)
        self._last_snapshot = None
        self._last_push = 0

    def _build_snapshot(self):
        active_ids = presence_service.get_online_user_ids()
        statuses = presence_service.get_statuses(active_ids)
        online = [uid for uid, s in statuses.items() if s == 'online']
        away = [uid for uid, s in statuses.items() if s == 'away']
        return {'online': online, 'away': away}

    def get_initial_events(self):
        snapshot = self._build_snapshot()
        self._last_snapshot = snapshot
        self._last_push = time.time()
        return [('presence_snapshot', snapshot, None)]

    def poll(self, cache_value):
        now = time.time()
        # Rebuild every ~10 seconds regardless of dirty flag
        if now - self._last_push < 10:
            return []
        self._last_push = now
        snapshot = self._build_snapshot()
        # Only emit if the snapshot changed
        if snapshot == self._last_snapshot:
            return []
        self._last_snapshot = snapshot
        return [('presence_snapshot', snapshot, None)]
