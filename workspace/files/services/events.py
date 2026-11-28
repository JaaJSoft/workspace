"""Service helpers for recording and reading file activity events.

Events are written from the request layer (viewsets, views) where the
acting user is naturally available. Read paths are exposed both as a
queryset helper (for the REST endpoint) and as a formatter that turns
raw events into UI-ready timeline rows.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Min, Q
from django.utils import timezone

from workspace.common.task_priority import LOW_PRIORITY

from ..models import File, FileEvent
from ..queries import FilePermission, access_arms, accessible_file_ids

logger = logging.getLogger(__name__)


# How many events a file's timeline lists, newest first.
TIMELINE_EVENTS_LIMIT = 200

# A repeat of one of these actions by the same person, within this long of
# the file's previous event, folds into that event instead of adding a row:
# the note editor saves every few seconds while someone types.
COALESCE_WINDOW = timedelta(minutes=5)
_COALESCED_ACTIONS = frozenset({FileEvent.Action.CONTENT_REPLACED})

# Longer than the note editor's 30 s save interval while someone keeps typing,
# so a whole writing session reaches its handlers once, after its last save.
SETTLE_SECONDS = 60


def record_event(file, actor, action, metadata=None, *, bulk=False, settle=False):
    """Persist a single audit row for an action performed on a file.

    Failures are logged and swallowed - audit logging must never bring
    down the user's primary action (rename, share, ...). On success, the
    event's handlers are scheduled to run after the transaction commits.

    A content replacement by the same actor shortly after the previous one
    renews that event rather than adding one (``COALESCE_WINDOW``).

    ``bulk=True`` marks the event as one of many from a long job nobody
    waits on file by file (an import): the tasks it sets off go out at low
    priority, behind those of the files people are uploading.

    ``settle=True`` holds the handlers back until the file's content has not
    changed for ``SETTLE_SECONDS``: each write queues them late, with the
    hash it wrote, and only the last write finds its hash still in place.
    """
    if file is None:
        return None
    # Skip unsaved instances. Real paths always persist the file before
    # calling here; the guard is for tests that mock ``tree.create_*``
    # to return a non-persisted File and would otherwise trigger a
    # deferred-FK violation at transaction commit.
    state = getattr(file, "_state", None)
    if state is not None and state.adding:
        return None
    if actor is not None and not actor.is_authenticated:
        actor = None
    try:
        event = _repeated_event(file, actor, action, metadata)
        if event is None:
            event = FileEvent.objects.create(
                file=file, actor=actor, action=action, metadata=metadata or {}
            )
        else:
            event.renew()
    except Exception:
        logger.exception("Failed to record file event %s for file %s", action, file.pk)
        return None
    _schedule_dispatch(event, bulk=bulk, settle=settle)
    return event


def _repeated_event(file, actor, action, metadata):
    """The file's newest event when *action* by *actor* repeats it, else None."""
    if action not in _COALESCED_ACTIONS or metadata:
        return None
    latest = FileEvent.objects.filter(file=file).order_by("-created_at").first()
    if (
        latest is None
        or latest.action != action
        or latest.actor_id != (actor.pk if actor is not None else None)
        or latest.created_at < timezone.now() - COALESCE_WINDOW
    ):
        return None
    latest.file = file
    return latest


def _schedule_dispatch(event, *, bulk, settle):
    """Run the event's handlers after the surrounding transaction commits.

    on_commit guarantees the worker sees committed data and that rolled-back
    mutations dispatch nothing. No-op when no handler is registered. Never
    raises - dispatch must not break the user's action.

    A bulk job already runs in a worker, so its handlers run in place rather
    than through a dispatch task of their own: one task less per file.
    """
    try:
        from workspace.files.services.event_dispatch import (
            has_handlers,
            run_event_handlers,
        )

        if not has_handlers(event.action):
            return
        if bulk:
            transaction.on_commit(lambda: run_event_handlers(event, LOW_PRIORITY))
            return
        from workspace.files.tasks import run_file_event_handlers

        event_uuid = str(event.uuid)
        content_hash = event.file.content_hash if settle else None

        def _enqueue_dispatch():
            try:
                if settle:
                    run_file_event_handlers.apply_async(
                        (event_uuid,),
                        {"content_hash": content_hash},
                        countdown=SETTLE_SECONDS,
                    )
                else:
                    run_file_event_handlers.delay(event_uuid)
            except Exception:
                logger.exception(
                    "Failed to enqueue file-event dispatch for %s", event_uuid
                )

        transaction.on_commit(_enqueue_dispatch)
    except Exception:
        logger.exception("Failed to schedule dispatch for file event %s", event.uuid)


def purge_expired_events():
    """Delete the events no surface shows any more; return how many went.

    An event goes once it is older than ``FILES_EVENT_RETENTION_DAYS``, which
    covers the activity feeds and the profile heatmap, and is not one of the
    ``TIMELINE_EVENTS_LIMIT`` newest of its file, which the file's timeline
    lists whatever their age. A file's newest event always stays, and its
    ``last_event_at`` keeps pointing at a real event.
    """
    cutoff = timezone.now() - timedelta(days=settings.FILES_EVENT_RETENTION_DAYS)
    crowded = list(
        FileEvent.objects.order_by()
        .values("file_id")
        .annotate(count=Count("pk"), oldest=Min("created_at"))
        .filter(count__gt=TIMELINE_EVENTS_LIMIT, oldest__lt=cutoff)
        .values_list("file_id", flat=True)
    )
    deleted = 0
    for file_id in crowded:
        events = FileEvent.objects.filter(file_id=file_id)
        shown = list(
            events.order_by("-created_at").values_list("pk", flat=True)[
                :TIMELINE_EVENTS_LIMIT
            ]
        )
        count, _ = events.filter(created_at__lt=cutoff).exclude(pk__in=shown).delete()
        deleted += count
    return deleted


# Who else holds the file and which public links reach it: the properties
# panel lists shares and links to the file's manager only, so the timeline
# keeps their history to them too. Dropped as a whole, never one by one, so a
# "permission changed" or "revoked" entry never outlives the share it names.
MANAGER_ONLY_ACTIONS = frozenset(
    {
        FileEvent.Action.SHARED,
        FileEvent.Action.SHARE_PERMISSION_CHANGED,
        FileEvent.Action.UNSHARED,
        FileEvent.Action.LINK_CREATED,
        FileEvent.Action.LINK_REVOKED,
        FileEvent.Action.LINK_UPLOAD,
    }
)

# Metadata keys only the file's manager may read: the file a copy was made from
# belongs to whoever copied it, and may sit in their private tree. A reader is
# told it is a copy (``copied``), not of what.
_MANAGER_ONLY_METADATA = ("source_name", "source_uuid")


def _is_manager(permission):
    return permission is not None and permission >= FilePermission.MANAGE


def events_for_file(file, permission):
    """Return the events of *file* a viewer at *permission* may read, newest first.

    Pair it with ``readable_events`` on the rows read, which strips what the
    viewer may not read from each event's metadata.
    """
    qs = (
        FileEvent.objects.filter(file=file)
        .select_related("actor")
        .order_by("-created_at")
    )
    if not _is_manager(permission):
        qs = qs.exclude(action__in=MANAGER_ONLY_ACTIONS)
    return qs


def readable_events(events, permission):
    """Return *events* with the metadata a viewer at *permission* may not read
    removed. Edits the instances in memory only: never save them afterwards.
    """
    events = list(events)
    if _is_manager(permission):
        return events
    for event in events:
        metadata = event.metadata or {}
        if any(key in metadata for key in _MANAGER_ONLY_METADATA):
            event.metadata = {
                key: value
                for key, value in metadata.items()
                if key not in _MANAGER_ONLY_METADATA
            }
            if "source_uuid" in metadata:
                event.metadata["copied"] = True
    return events


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
    arms = [{}] if viewer is None else access_arms(viewer)
    if exclude_actor_id is None or (
        viewer is not None and viewer.pk != exclude_actor_id
    ):
        return [(Q(**arm), "last_event_at") for arm in arms]
    if viewer is None:
        ranked = [(Q(owner_id=exclude_actor_id), "last_foreign_event_at")]
    else:
        ranked = []
    for arm in arms:
        if "owner" in arm:
            ranked.append((Q(**arm), "last_foreign_event_at"))
            continue
        ranked.append((Q(**arm) & ~Q(owner_id=exclude_actor_id), "last_event_at"))
        if "group_id" in arm:
            # The viewer's own nodes in that group: the owner arm only spans
            # personal ones, since owning a group node stops counting once its
            # owner leaves the group.
            ranked.append(
                (Q(owner_id=exclude_actor_id, **arm), "last_foreign_event_at")
            )
    return ranked


def recent_feed_events(
    feed_q, *, owner_id=None, viewer=None, exclude_actor_id=None, limit=10, offset=0
):
    """Newest events on files matching *feed_q*, for an activity feed.

    *feed_q* is ``FILE_FEED_Q`` or ``NOTE_FEED_Q``. *owner_id* narrows to one
    user's files; *viewer* (``None`` for no access check) narrows to the files
    they can access, per ``access_arms``; *exclude_actor_id* drops
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
        files = files.filter(pk__in=accessible_file_ids(viewer))
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
