from django.core.cache import cache

from workspace.core.sse_registry import SSEProvider

from .services.progress import PENDING_EVENTS_KEY


class ImportsSSEProvider(SSEProvider):
    def get_initial_events(self):
        return []

    def poll(self, cache_value):
        if cache_value is None:
            return []
        key = PENDING_EVENTS_KEY.format(user_id=self.user.id)
        events = cache.get(key, [])
        if not events:
            return []
        cache.delete(key)
        return [(event["type"], event, None) for event in events]
