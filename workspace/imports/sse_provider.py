from workspace.core.sse_registry import SSEProvider, drain_user_events

from .services.progress import SLUG


class ImportsSSEProvider(SSEProvider):
    def get_initial_events(self):
        return []

    def poll(self, cache_value):
        if cache_value is None:
            return []
        return [
            (event["type"], event, None)
            for event in drain_user_events(SLUG, self.user.id)
        ]
