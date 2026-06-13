"""Service helpers for recording and reading file activity events.

Events are written from the request layer (viewsets, views) where the
acting user is naturally available. Read paths are exposed both as a
queryset helper (for the REST endpoint) and as a formatter that turns
raw events into UI-ready timeline rows.
"""

import logging

from django.db import transaction

from ..models import FileEvent

logger = logging.getLogger(__name__)


def record_event(file, actor, action, metadata=None):
    """Persist a single audit row for an action performed on a file.

    Failures are logged and swallowed - audit logging must never bring
    down the user's primary action (rename, share, ...). On success, the
    event's handlers are scheduled to run after the transaction commits.
    """
    if file is None:
        return None
    # Skip unsaved instances. Real paths always persist the file before
    # calling here; the guard is for tests that mock FileService.create_*
    # to return a non-persisted File and would otherwise trigger a
    # deferred-FK violation at transaction commit.
    state = getattr(file, "_state", None)
    if state is not None and state.adding:
        return None
    try:
        event = FileEvent.objects.create(
            file=file,
            actor=actor if (actor is not None and actor.is_authenticated) else None,
            action=action,
            metadata=metadata or {},
        )
    except Exception:
        logger.exception("Failed to record file event %s for file %s", action, file.pk)
        return None
    _schedule_dispatch(event)
    return event


def _schedule_dispatch(event):
    """Run the event's handlers after the surrounding transaction commits.

    on_commit guarantees the worker sees committed data and that rolled-back
    mutations dispatch nothing. No-op when no handler is registered. Never
    raises - dispatch must not break the user's action.
    """
    try:
        from workspace.files.services.event_dispatch import has_handlers

        if not has_handlers(event.action):
            return
        from workspace.files.tasks import run_file_event_handlers

        transaction.on_commit(lambda: run_file_event_handlers.delay(str(event.uuid)))
    except Exception:
        logger.exception("Failed to schedule dispatch for file event %s", event.uuid)


def events_for_file(file):
    """Return the queryset of events for *file*, newest first."""
    return (
        FileEvent.objects.filter(file=file)
        .select_related("actor")
        .order_by("-created_at")
    )


def serialize_event(event):
    """Serialize an event for API responses and template rendering.

    The icon and label come from ``FileEvent`` (single source of truth in
    ``models.py``); this function only assembles the JSON-friendly dict.
    """
    actor = event.actor
    actor_data = None
    if actor is not None:
        actor_data = {
            "id": actor.pk,
            "username": actor.username,
            "full_name": actor.get_full_name() or actor.username,
        }
    return {
        "uuid": str(event.uuid),
        "action": event.action,
        "label": event.short_label,
        "icon": event.icon,
        "actor": actor_data,
        "metadata": event.metadata or {},
        "created_at": event.created_at.isoformat(),
    }
