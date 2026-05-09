"""Service helpers for recording and reading file activity events.

Events are written from the request layer (viewsets, views) where the
acting user is naturally available. Read paths are exposed both as a
queryset helper (for the REST endpoint) and as a formatter that turns
raw events into UI-ready timeline rows.
"""

import logging

from ..models import FileEvent

logger = logging.getLogger(__name__)


def record_event(file, actor, action, metadata=None):
    """Persist a single audit row for an action performed on a file.

    Failures are logged and swallowed - audit logging must never bring
    down the user's primary action (rename, share, ...).
    """
    if file is None:
        return None
    # Skip unsaved instances. Real paths always persist the file before
    # calling here; the guard is for tests that mock FileService.create_*
    # to return a non-persisted File and would otherwise trigger a
    # deferred-FK violation at transaction commit.
    state = getattr(file, '_state', None)
    if state is not None and state.adding:
        return None
    try:
        return FileEvent.objects.create(
            file=file,
            actor=actor if (actor is not None and actor.is_authenticated) else None,
            action=action,
            metadata=metadata or {},
        )
    except Exception:
        logger.exception("Failed to record file event %s for file %s", action, file.pk)
        return None


def events_for_file(file):
    """Return the queryset of events for *file*, newest first."""
    return FileEvent.objects.filter(file=file).select_related('actor').order_by('-created_at')


_HUMAN_LABELS = {
    FileEvent.Action.CREATED: 'created this',
    FileEvent.Action.RENAMED: 'renamed this',
    FileEvent.Action.MOVED: 'moved this',
    FileEvent.Action.CONTENT_REPLACED: 'replaced the content',
    FileEvent.Action.DELETED: 'moved this to the trash',
    FileEvent.Action.RESTORED: 'restored this from the trash',
    FileEvent.Action.SHARED: 'shared this',
    FileEvent.Action.SHARE_PERMISSION_CHANGED: 'updated share permissions',
    FileEvent.Action.UNSHARED: 'revoked a share',
    FileEvent.Action.LINK_CREATED: 'created a public link',
    FileEvent.Action.LINK_REVOKED: 'revoked a public link',
}

_ICONS = {
    FileEvent.Action.CREATED: 'plus-circle',
    FileEvent.Action.RENAMED: 'pencil',
    FileEvent.Action.MOVED: 'move',
    FileEvent.Action.CONTENT_REPLACED: 'upload',
    FileEvent.Action.DELETED: 'trash-2',
    FileEvent.Action.RESTORED: 'rotate-ccw',
    FileEvent.Action.SHARED: 'user-plus',
    FileEvent.Action.SHARE_PERMISSION_CHANGED: 'shield',
    FileEvent.Action.UNSHARED: 'user-minus',
    FileEvent.Action.LINK_CREATED: 'link',
    FileEvent.Action.LINK_REVOKED: 'unlink',
}


def serialize_event(event):
    """Serialize an event for API responses and template rendering."""
    actor = event.actor
    actor_data = None
    if actor is not None:
        actor_data = {
            'id': actor.pk,
            'username': actor.username,
            'full_name': actor.get_full_name() or actor.username,
        }
    return {
        'uuid': str(event.uuid),
        'action': event.action,
        'label': _HUMAN_LABELS.get(event.action, event.action),
        'icon': _ICONS.get(event.action, 'activity'),
        'actor': actor_data,
        'metadata': event.metadata or {},
        'created_at': event.created_at.isoformat(),
    }
