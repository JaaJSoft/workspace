"""Service helpers for recording and reading file activity events.

Events are written from the request layer (viewsets, views) where the
acting user is naturally available. Read paths are exposed both as a
queryset helper (for the REST endpoint) and as a formatter that turns
raw events into UI-ready timeline rows.
"""

import logging

from django.db import transaction
from django.db.models import Q

from ..models import File, FileEvent

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

        event_uuid = str(event.uuid)

        def _enqueue_dispatch():
            try:
                run_file_event_handlers.delay(event_uuid)
            except Exception:
                logger.exception(
                    "Failed to enqueue file-event dispatch for %s", event_uuid
                )

        transaction.on_commit(_enqueue_dispatch)
    except Exception:
        logger.exception("Failed to schedule dispatch for file event %s", event.uuid)


def events_for_file(file):
    """Return the queryset of events for *file*, newest first."""
    return (
        FileEvent.objects.filter(file=file)
        .select_related("actor")
        .order_by("-created_at")
    )


# Past this many candidate files per arm, widening costs more than reading the
# accessible set once, and the id list nears the SQL parameter limit.
_MAX_FEED_CANDIDATES = 4096


def _feed_arms(viewer, exclude_actor_id):
    """``(filter, ranking column)`` for each arm of a feed read.

    When the excluded actor is the viewer (the dashboard) or there is no
    viewer, the excluded actor's own files are ranked by their newest event by
    someone else, and dropped from every other arm: ranked by their newest
    event, they would surface the very events the feed hides.
    """
    from .files import FileService

    arms = [{}] if viewer is None else FileService.access_arms(viewer)
    if exclude_actor_id is None or (
        viewer is not None and viewer.pk != exclude_actor_id
    ):
        return [(Q(**arm), "last_event_at") for arm in arms]
    ranked = [(Q(owner_id=exclude_actor_id), "last_foreign_event_at")]
    for arm in arms:
        if arm.keys() != {"owner"}:
            ranked.append((Q(**arm) & ~Q(owner_id=exclude_actor_id), "last_event_at"))
    return ranked


def recent_feed_events(
    feed_q, *, owner_id=None, viewer=None, exclude_actor_id=None, limit=10, offset=0
):
    """Newest events on files matching *feed_q*, for an activity feed.

    *feed_q* is ``FILE_FEED_Q`` or ``NOTE_FEED_Q``. *owner_id* narrows to one
    user's files; *viewer* (``None`` for no access check) narrows to the files
    they can access, per ``FileService.access_arms``; *exclude_actor_id* drops
    that user's events (system events, with no actor, stay).

    Each arm yields a batch of its files ranked by newest event, through the
    File recent-event indexes, and only those files' events are read: the cost
    follows the page, not the number of accessible files. Nothing outside the
    batch is newer than the last file of a full arm (the floor), so the
    candidates' events from the floor up are exact, and the first batch fills
    the page whenever each file's ranking column is its newest kept event.
    When it is not (the viewer's events on someone else's file, a backdated
    event), the batch widens until the page fills, then gives way to a read
    of the whole accessible set.
    """
    from .files import FileService

    needed = offset + limit
    if needed <= 0:
        return []
    files = File.objects.filter(feed_q, last_event_at__isnull=False)
    if owner_id is not None:
        files = files.filter(owner_id=owner_id)
    arms = _feed_arms(viewer, exclude_actor_id)
    events = FileEvent.objects.select_related("actor", "file").order_by("-created_at")
    if exclude_actor_id is not None:
        events = events.exclude(actor_id=exclude_actor_id)

    batch = needed
    while batch <= _MAX_FEED_CANDIDATES:
        candidate_ids, floor = set(), None
        for arm, column in arms:
            newest = list(
                files.filter(arm, **{f"{column}__isnull": False})
                .order_by(f"-{column}")
                .values_list("pk", column)[:batch]
            )
            candidate_ids.update(pk for pk, _ in newest)
            if len(newest) == batch:
                arm_floor = newest[-1][1]
                floor = arm_floor if floor is None else max(floor, arm_floor)
        page = events.filter(file_id__in=candidate_ids)
        if floor is not None:
            page = page.filter(created_at__gte=floor)
        page = list(page[:needed])
        if floor is None or len(page) == needed:
            return page[offset:]
        batch *= 4

    if viewer is not None:
        files = files.filter(pk__in=FileService.accessible_file_ids(viewer))
    return list(events.filter(file__in=files)[offset:needed])


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
