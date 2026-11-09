"""Batch file operations, run off-request one item at a time.

Every action a user takes on files they picked - move, copy, trash, restore,
purge, extract, emptying the trash - is an operation. A request only records
what was asked (:func:`create_operation`); a worker then walks the list in
time slices (:func:`run_slice`), reporting progress to the owner as it goes.
Each item is decided when its turn comes, by the per-item service of its
kind, against the rights and the folders of that moment - never against what
the request saw.
"""

import logging
from datetime import timedelta
from enum import Enum
from itertools import batched
from time import monotonic

from celery.exceptions import SoftTimeLimitExceeded
from django.utils import timezone

from workspace.core.sse_registry import push_user_event

from ..models import File, FileOperation
from ..sse_provider import SLUG
from .extract import extract_item
from .files import FileService
from .quota import subtree_bytes
from .refusals import Refusal, Refused
from .transfers import OnConflict, Outcome, copy_item, move_item
from .trash import purge_item, restore_item, trash_item

logger = logging.getLogger(__name__)

MAX_ITEMS = 2000
MAX_ACTIVE_PER_USER = 10
# How long a slice works before handing the worker back. An item in flight
# is never cut short, so one slice lasts as long as its longest item.
SLICE_SECONDS = 20
# Longer than the hard time limit of a task: past it nothing can still be
# running the operation.
STALE_AFTER = timedelta(minutes=35)
RETENTION = timedelta(hours=24)

_PROGRESS_INTERVAL = 0.4
_MAX_ERRORS = 50
_MEASURE_BATCH = 500

# Reasons an item fails that no transfer refusal covers.
NOT_FOUND = "not_found"
TIMEOUT = "timeout"
FAILED = "failed"

Kind = FileOperation.Kind

# Kinds that put something into a destination folder.
TARGETED_KINDS = frozenset({Kind.MOVE, Kind.COPY, Kind.EXTRACT})
# Kinds that only take a name collision into account.
TRANSFER_KINDS = frozenset({Kind.MOVE, Kind.COPY})
# Kinds whose items are in the trash.
TRASHED_KINDS = frozenset({Kind.RESTORE, Kind.PURGE, Kind.EMPTY_TRASH})

_DEFAULT_ON_CONFLICT = {Kind.COPY: OnConflict.RENAME, Kind.MOVE: OnConflict.ERROR}


class TargetUnavailable(Exception):
    """The destination folder is missing or the user may not add to it."""


class TooManyOperations(Exception):
    """The user already has as many operations in progress as allowed."""


class Slice(Enum):
    FINISHED = "finished"
    PAUSED = "paused"


class _Stopped(Exception):
    """Raised from a checkpoint to abandon the item in flight."""


def create_operation(user, kind, items, target_uuid=None):
    """Record a batch for *user* and return it, still pending.

    *items* is a list of ``{"uuid", "on_conflict"}`` whose shape the caller
    validated; emptying the trash takes none and lists the user's trash here.
    Only the destination is judged now: whether each item exists and may be
    acted on is the runner's call, so that an item out of reach and an item
    that does not exist end up reported the same way.
    """
    target = None
    if kind in TARGETED_KINDS and target_uuid is not None:
        target = File.objects.filter(pk=target_uuid).first()
        if target is None or not FileService.can_create_in(user, target):
            raise TargetUnavailable
    active = FileOperation.objects.filter(
        owner=user, status__in=FileOperation.ACTIVE_STATUSES
    ).count()
    if active >= MAX_ACTIVE_PER_USER:
        raise TooManyOperations
    if kind == Kind.EMPTY_TRASH:
        items = [{"uuid": pk} for pk in _trash_roots(user)]
    default = _DEFAULT_ON_CONFLICT.get(kind)
    return FileOperation.objects.create(
        owner=user,
        kind=kind,
        target_uuid=target.pk if target is not None else None,
        target_name=target.name if target is not None else "",
        items=[_stored_item(item, default) for item in items],
        total=len(items),
    )


def _stored_item(item, default_on_conflict):
    stored = {"uuid": str(item["uuid"])}
    if default_on_conflict is not None:
        stored["on_conflict"] = str(item.get("on_conflict") or default_on_conflict)
    return stored


def _trash_roots(user):
    """The trashed nodes of *user* that emptying their trash purges.

    Only their own: the trash also lists what other members of their groups
    trashed, and emptying it must not destroy that. A node inside another of
    their trashed folders goes with that folder.
    """
    trashed = FileService.owned_files_qs(user).filter(deleted_at__isnull=False)
    return list(
        trashed.exclude(parent__in=trashed.values("pk")).values_list("pk", flat=True)
    )


def request_cancel(operation):
    """Ask the runner to stop *operation*; what it already did stays done."""
    now = timezone.now()
    # Nothing has started yet, so there is no runner to wait for.
    cancelled = FileOperation.objects.filter(
        pk=operation.pk, status=FileOperation.Status.PENDING
    ).update(
        status=FileOperation.Status.CANCELLED,
        cancel_requested_at=now,
        finished_at=now,
    )
    if not cancelled:
        FileOperation.objects.filter(
            pk=operation.pk,
            status=FileOperation.Status.RUNNING,
            cancel_requested_at__isnull=True,
        ).update(cancel_requested_at=now)
    operation.refresh_from_db()
    if cancelled:
        push_progress(operation)
    return operation


def progress_event(operation):
    """What the owner's open pages are told about *operation*.

    Errors are left out: their wording depends on the reader's language,
    which only a request knows.
    """
    return {
        "type": "operation",
        "operation_uuid": str(operation.pk),
        "kind": operation.kind,
        "status": operation.status,
        "total": operation.total,
        "processed": operation.processed,
        "bytes_total": operation.bytes_total,
        "bytes_done": operation.bytes_done,
        "counts": operation.counts,
    }


def push_progress(operation):
    push_user_event(
        SLUG,
        operation.owner_id,
        progress_event(operation),
        supersedes=("operation_uuid", str(operation.pk)),
    )


def run_slice(operation_uuid):
    """Work on an operation for one slice; ``PAUSED`` asks to be called again."""
    claimed = FileOperation.objects.filter(
        pk=operation_uuid, status__in=FileOperation.ACTIVE_STATUSES
    ).update(status=FileOperation.Status.RUNNING, heartbeat_at=timezone.now())
    if not claimed:
        return Slice.FINISHED
    operation = FileOperation.objects.select_related("owner").get(pk=operation_uuid)
    return _Runner(operation).run()


def fail_stale_operations():
    """Close the operations whose runner is gone, and return how many.

    They are not resumed: the item that was in flight may be half done, and
    running it again would copy it a second time.
    """
    cutoff = timezone.now() - STALE_AFTER
    stale = FileOperation.objects.filter(
        status__in=FileOperation.ACTIVE_STATUSES, heartbeat_at__lt=cutoff
    )
    failed = 0
    for operation in stale:
        closed = FileOperation.objects.filter(
            pk=operation.pk,
            status=operation.status,
            heartbeat_at=operation.heartbeat_at,
        ).update(status=FileOperation.Status.FAILED, finished_at=timezone.now())
        if closed:
            operation.status = FileOperation.Status.FAILED
            push_progress(operation)
            failed += 1
    return failed


def purge_finished_operations():
    """Delete the reports nobody dismissed, and return how many."""
    deleted, _ = FileOperation.objects.filter(
        finished_at__lt=timezone.now() - RETENTION
    ).delete()
    return deleted


def _weight(node):
    """Bytes a copy of *node* writes."""
    if node.node_type == File.NodeType.FOLDER:
        return subtree_bytes(node, include_trashed=False)
    if node.node_type == File.NodeType.FILE:
        return node.size or 0
    return 0


class _Runner:
    def __init__(self, operation):
        self.operation = operation
        self.user = operation.owner
        self.measures_bytes = operation.kind == Kind.COPY
        self.in_trash = operation.kind in TRASHED_KINDS
        self._deadline = monotonic() + SLICE_SECONDS
        self._last_tick = monotonic()
        self._yield = False
        # Bytes done before the item in flight, and what that item weighs.
        self._bytes_before = operation.bytes_done
        self._item_weight = 0

    def run(self):
        operation = self.operation
        if not self.user.is_active:
            self._finish(FileOperation.Status.FAILED)
            return Slice.FINISHED
        if self.measures_bytes and operation.bytes_total is None:
            operation.bytes_total = self._measure()
            self._write(["bytes_total"])
        try:
            self._stop_if_asked()
            while operation.processed < len(operation.items):
                self._tick()
                self._process(operation.items[operation.processed])
                operation.processed += 1
                # Judged after an item, never before: a slice that starts
                # late still gets one item further.
                out_of_time = self._yield or monotonic() > self._deadline
                if out_of_time and operation.processed < len(operation.items):
                    self._write_progress()
                    return Slice.PAUSED
        except _Stopped as stopped:
            if stopped.args:
                self._finish(stopped.args[0])
            return Slice.FINISHED
        self._finish(FileOperation.Status.COMPLETED)
        return Slice.FINISHED

    def _measure(self):
        total = 0
        uuids = [item["uuid"] for item in self.operation.items]
        for batch in batched(uuids, _MEASURE_BATCH, strict=False):
            nodes = list(File.objects.filter(pk__in=batch, deleted_at__isnull=True))
            permissions = FileService.get_permissions_bulk(self.user, nodes)
            total += sum(
                _weight(node) for node in nodes if permissions[node.pk] is not None
            )
        return total

    def _process(self, item):
        operation = self.operation
        node = File.objects.filter(
            pk=item["uuid"], deleted_at__isnull=not self.in_trash
        ).first()
        permission = (
            FileService.trash_permission
            if self.in_trash
            else FileService.get_permission
        )
        if node is None or permission(self.user, node) is None:
            self._record_failure(item, None, NOT_FOUND)
            return
        self._bytes_before = operation.bytes_done
        self._item_weight = _weight(node) if self.measures_bytes else 0
        try:
            outcome = self._act(node, item)
        except Refused as refusal:
            self._record_failure(item, node, refusal.reason)
        except _Stopped:
            operation.bytes_done = self._bytes_before
            raise
        except SoftTimeLimitExceeded:
            # The task has little time left: hand over once this is recorded.
            self._yield = True
            self._record_failure(item, node, TIMEOUT)
        except Exception:
            logger.exception("File operation %s failed on an item", operation.pk)
            self._record_failure(item, node, FAILED)
        else:
            self._count(outcome)
        operation.bytes_done = self._bytes_before + self._item_weight

    def _act(self, node, item):
        """Hand *node* to the service of the operation's kind."""
        user = self.user
        kind = self.operation.kind
        if kind == Kind.COPY:
            return copy_item(
                user, node, self._target(), item["on_conflict"], checkpoint=self._copied
            ).outcome
        if kind == Kind.MOVE:
            return move_item(user, node, self._target(), item["on_conflict"]).outcome
        if kind == Kind.EXTRACT:
            extract_item(user, node, self._target())
        elif kind == Kind.TRASH:
            trash_item(user, node)
        elif kind == Kind.RESTORE:
            restore_item(user, node)
        else:
            purge_item(user, node)
        return Outcome.DONE

    def _target(self):
        """The destination folder, read again for every item.

        It may have been trashed, or the user's access to it withdrawn, since
        the previous one: a destination that is gone refuses the item, it
        never turns into the root.
        """
        target_uuid = self.operation.target_uuid
        if target_uuid is None:
            return None
        target = File.objects.filter(pk=target_uuid).first()
        if target is None:
            raise Refused(Refusal.TARGET)
        return target

    def _copied(self, node):
        """Checkpoint of a copy: one more node of the item in flight exists."""
        operation = self.operation
        if node.node_type == File.NodeType.FILE:
            operation.bytes_done = min(
                operation.bytes_done + (node.size or 0),
                self._bytes_before + self._item_weight,
            )
        self._tick()

    def _tick(self):
        """Every so often, publish the progress and look for a cancellation."""
        now = monotonic()
        if now - self._last_tick < _PROGRESS_INTERVAL:
            return
        self._last_tick = now
        self._stop_if_asked()
        self._write_progress()

    def _stop_if_asked(self):
        state = (
            FileOperation.objects.filter(pk=self.operation.pk)
            .values_list("status", "cancel_requested_at")
            .first()
        )
        if state is None or state[0] != FileOperation.Status.RUNNING:
            # Deleted with its owner, or closed by the recovery sweep.
            raise _Stopped
        if state[1] is not None:
            raise _Stopped(FileOperation.Status.CANCELLED)

    def _count(self, outcome):
        counts = self.operation.counts
        counts[str(outcome)] = counts.get(str(outcome), 0) + 1

    def _record_failure(self, item, node, reason):
        self._count(FAILED)
        errors = self.operation.errors
        if len(errors) < _MAX_ERRORS:
            errors.append(
                {
                    "uuid": item["uuid"],
                    # Only a node the user can see has a name to show.
                    "name": node.name if node is not None else "",
                    "reason": str(reason),
                }
            )

    def _write(self, fields):
        """Write *fields*, unless the operation stopped running meanwhile."""
        operation = self.operation
        return FileOperation.objects.filter(
            pk=operation.pk, status=FileOperation.Status.RUNNING
        ).update(**{field: getattr(operation, field) for field in fields})

    def _write_progress(self):
        self.operation.heartbeat_at = timezone.now()
        self._write(["processed", "bytes_done", "counts", "errors", "heartbeat_at"])
        push_progress(self.operation)

    def _finish(self, status):
        operation = self.operation
        if (
            status == FileOperation.Status.COMPLETED
            and operation.bytes_total is not None
        ):
            # Sizes were measured before the run and may have moved since.
            operation.bytes_done = operation.bytes_total
        fields = ["processed", "bytes_done", "counts", "errors"]
        values = {field: getattr(operation, field) for field in fields}
        closed = FileOperation.objects.filter(
            pk=operation.pk, status=FileOperation.Status.RUNNING
        ).update(status=status, finished_at=timezone.now(), **values)
        if closed:
            operation.status = status
            push_progress(operation)
