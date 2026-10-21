import time

from workspace.core.sse_registry import SSEProvider
from workspace.users.services import presence_stream

# Statuses also change by the clock alone (online turns away after two idle
# minutes), so a stream re-reads them on a timer rather than on a notification.
POLL_INTERVAL = 10  # seconds


class PresenceSSEProvider(SSEProvider):
    """Streams the presence of the users the viewer watches.

    One ``presence_snapshot`` on connect, then ``presence_changes`` carrying
    only the statuses that moved since the last event.
    """

    def __init__(self, user, last_event_id):
        super().__init__(user, last_event_id)
        self._watched = set()
        self._sent = {}
        self._last_check = 0

    def get_initial_events(self):
        self._watched = presence_stream.watched_user_ids(self.user.id)
        self._sent = presence_stream.statuses_of(self._watched)
        self._last_check = time.monotonic()
        return [("presence_snapshot", presence_stream.as_snapshot(self._sent), None)]

    def poll(self, cache_value):
        if cache_value is not None:
            # The viewer's pages watched new ids: answer for them right away.
            self._watched = presence_stream.watched_user_ids(self.user.id)
        elif time.monotonic() - self._last_check < POLL_INTERVAL:
            return []
        self._last_check = time.monotonic()

        current = presence_stream.statuses_of(self._watched)
        changes = {
            uid: status
            for uid, status in current.items()
            if self._sent.get(uid) != status
        }
        self._sent = current
        if not changes:
            return []
        return [("presence_changes", presence_stream.as_payload(changes), None)]
