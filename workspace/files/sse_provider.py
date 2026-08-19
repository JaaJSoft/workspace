import logging

from workspace.core.sse_registry import SSEProvider, drain_user_events, push_user_event

logger = logging.getLogger(__name__)

SLUG = "files"


class FilesSSEProvider(SSEProvider):
    def get_initial_events(self):
        return []

    def poll(self, cache_value):
        if cache_value is None:
            return []
        return [(ev["type"], ev, None) for ev in drain_user_events(SLUG, self.user.id)]


def push_file_event(file_obj, event_type, actor_username, exclude_user_id=None):
    """Push an SSE event to all users with access to this file."""
    from workspace.files.models import FileShare

    user_ids = {file_obj.owner_id}
    shared_ids = FileShare.objects.filter(
        file=file_obj,
    ).values_list("shared_with_id", flat=True)
    user_ids.update(shared_ids)

    if exclude_user_id:
        user_ids.discard(exclude_user_id)

    event = {
        "type": event_type,
        "file_uuid": str(file_obj.uuid),
        "actor": actor_username,
    }

    for uid in user_ids:
        push_user_event(SLUG, uid, event)
