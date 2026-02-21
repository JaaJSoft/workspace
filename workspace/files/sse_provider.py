import logging

from django.core.cache import cache

from workspace.core.sse_registry import SSEProvider, notify_sse

logger = logging.getLogger(__name__)

PENDING_EVENTS_KEY = 'files:pending_events:{user_id}'
PENDING_EVENTS_TTL = 300  # 5 min


class FilesSSEProvider(SSEProvider):
    def get_initial_events(self):
        return []

    def poll(self, cache_value):
        if cache_value is None:
            return []
        key = PENDING_EVENTS_KEY.format(user_id=self.user.id)
        raw = cache.get(key, [])
        if not raw:
            return []
        cache.delete(key)
        return [(ev['type'], ev, None) for ev in raw]


def push_file_event(file_obj, event_type, actor_username, exclude_user_id=None):
    """Push an SSE event to all users with access to this file."""
    from workspace.files.models import FileShare

    user_ids = {file_obj.owner_id}
    shared_ids = FileShare.objects.filter(
        file=file_obj,
    ).values_list('shared_with_id', flat=True)
    user_ids.update(shared_ids)

    if exclude_user_id:
        user_ids.discard(exclude_user_id)

    event = {
        'type': event_type,
        'file_uuid': str(file_obj.uuid),
        'actor': actor_username,
    }

    for uid in user_ids:
        key = PENDING_EVENTS_KEY.format(user_id=uid)
        existing = cache.get(key, [])
        existing.append(event)
        cache.set(key, existing, PENDING_EVENTS_TTL)
        notify_sse('files', uid)
