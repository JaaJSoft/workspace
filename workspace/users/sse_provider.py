import time
import logging
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

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
        """Build presence snapshot with a single DB query."""
        from workspace.users.models import UserPresence

        now = timezone.now()
        cutoff = now - presence_service.AWAY_THRESHOLD
        online_cutoff = now - presence_service.ONLINE_THRESHOLD

        # Single query: all users who should appear in presence lists
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
                # else: offline, skip

        return {'online': online, 'away': away, 'busy': busy}

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
