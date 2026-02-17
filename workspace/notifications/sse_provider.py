import logging
from workspace.core.sse_registry import SSEProvider
from .services import get_unread_count

logger = logging.getLogger(__name__)


class NotificationsSSEProvider(SSEProvider):

    def get_initial_events(self):
        try:
            count = get_unread_count(self.user)
            return [('count', {'unread': count}, None)]
        except Exception:
            logger.exception('Failed initial notification count for user %s', self.user.id)
            return []

    def poll(self, cache_value):
        if cache_value is None:
            return []
        try:
            count = get_unread_count(self.user)
            return [('count', {'unread': count}, None)]
        except Exception:
            logger.exception('Failed notification poll for user %s', self.user.id)
            return []
