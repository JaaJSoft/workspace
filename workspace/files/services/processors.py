"""Registry of the processors that derive data from a file's content.

A processor computes something from a file's bytes - its malware verdict, its
thumbnail, its MediaInfo row, its photo-library row... - and runs from two
places:

- the upload pipeline (run_pipeline, the files.process_file task), queued once
  an upload or a content replacement commits. It runs every processor that
  declares ``applies_to``, in ``order``, and walks the file's
  ``processing_status`` from pending to ready;
- the hourly catch-up, the safety net for everything that path missed: files
  older than the processor, a lost dispatch, a blob that could not be read, a
  processor whose output format changed. It owns paging, queueing one task per
  file, the bound, the priority and the expiry (see files/tasks.py).

Both run a processor through run_processor, which keeps its failure ledger
(services/processing_failures.py): an exception escaping ``process`` is a
failed attempt, and a file that burned its budget drops out of the
processor's pending set until the parking expires. Registering a processor is
all it takes to be dispatched, retried and parked.
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils import timezone

from workspace.common.logging import scrub
from workspace.common.uuids import parse_uuid_or_none

from ..models import File, FileEvent
from .event_dispatch import on_file_event

if TYPE_CHECKING:
    from django.db.models import QuerySet

logger = logging.getLogger(__name__)

# Rows fetched per keyset page by pending_ids. A read cursor held open across
# the write transactions of a loop over it makes SQLite raise "database is
# locked" (tasks run inline in development), so the selection is paged rather
# than streamed.
_PAGE_SIZE = 200

# The last file a pass queued, per processor: the next pass starts right after
# it. Starting from the smallest uuid every time would hand each pass the same
# oldest files, and a processor with more files that keep failing than a pass
# takes would never reach the ones behind them.
_CURSOR_KEY = "files:catch_up:{}:cursor"

# The malware scan runs first: every processor after it reads the verdict
# (through is_blocked) before decoding the bytes, so an infected upload is
# never thumbnailed or probed in the first place.
SCAN_ORDER = 0
DEFAULT_ORDER = 100

# How long a file may sit in pending or processing before the catch-up assumes
# its pipeline task was lost (a worker killed, a broker flushed) and queues it
# again. Long enough for a slow scan of a large upload not to run twice.
STALLED_AFTER = timedelta(minutes=15)


class PendingQuery(Protocol):
    def __call__(self, *, reanalyze: bool = False) -> QuerySet[File]:
        """Without *reanalyze*, the files whose derived data is missing or
        stale; with it, every file the processor reads, up to date or not."""


class Process(Protocol):
    def __call__(self, file_obj: File, /) -> bool:
        """Compute and store *file_obj*'s derived data.

        False when nothing was stored and nothing went wrong (the content
        changed mid-read, a tool is not installed): the file stays pending for
        a later pass. A failure raises, and counts against the budget.
        """


def _always():
    return True


@dataclass(frozen=True)
class Processor:
    name: str
    pending: PendingQuery
    process: Process
    # Whether the upload pipeline runs this processor on a file. None leaves
    # it to the catch-up and to whatever other trigger it has (the content
    # hash is computed inline by every write, the search index follows renames
    # too).
    applies_to: Callable[[File], bool] | None = None
    # Drops the derived data of a file the processor no longer applies to: a
    # photo overwritten with a text file leaves the library.
    forget: Callable[[File], None] | None = None
    # Hands the file to a task of the processor's own instead of running it
    # inside the pipeline. For work heavy enough to hold up every processor
    # behind it (face detection); such a processor does not delay "ready".
    enqueue: Callable[[File], None] | None = None
    # False on a deployment where the processor cannot run (no ffprobe,
    # scanning switched off): nothing is pending then, so nothing is queued
    # for nothing.
    enabled: Callable[[], bool] = field(default=_always)
    order: int = DEFAULT_ORDER

    def pending_files(self, *, reanalyze=False):
        """The processor's pending files, or none while it is disabled.

        Files parked after repeated failures are left out until their parking
        expires, unless *reanalyze* asks for every file.
        """
        if not self.enabled():
            return File.objects.none()
        qs = self.pending(reanalyze=reanalyze)
        if reanalyze:
            return qs
        from .processing_failures import parked_file_ids

        return qs.exclude(uuid__in=parked_file_ids(self.name))


_PROCESSORS: dict[str, Processor] = {}


def register_processor(
    name,
    *,
    pending,
    process,
    applies_to=None,
    forget=None,
    enqueue=None,
    enabled=_always,
    order=DEFAULT_ORDER,
):
    """Declare a processor; return the registered entry."""
    processor = Processor(
        name=name,
        pending=pending,
        process=process,
        applies_to=applies_to,
        forget=forget,
        enqueue=enqueue,
        enabled=enabled,
        order=order,
    )
    _PROCESSORS[name] = processor
    return processor


def registered_processors():
    """Every processor, in pipeline order (registration order breaks ties)."""
    return sorted(_PROCESSORS.values(), key=lambda processor: processor.order)


def get_processor(name):
    return _PROCESSORS.get(name)


def resolve(names):
    """The processors *names* designate - all of them when empty - and the unknown names."""
    if not names:
        return registered_processors(), []
    names = list(dict.fromkeys(names))
    unknown = [name for name in names if name not in _PROCESSORS]
    return [_PROCESSORS[name] for name in names if name in _PROCESSORS], unknown


def run_processor(processor, file_obj):
    """Run *processor* on *file_obj* and keep its failure ledger; True when stored.

    Never raises: a failure is logged and recorded as an attempt. The ledger
    writes are an optimisation, not the product, so they are guarded too - a
    file hard-deleted mid-run breaks the foreign key, and an escaping
    IntegrityError would abandon the rest of the pipeline.
    """
    from . import processing_failures

    try:
        stored = processor.process(file_obj)
    except Exception as exc:
        logger.warning(
            "Processor %s failed on %s",
            processor.name,
            scrub(file_obj.uuid),
            exc_info=True,
        )
        try:
            processing_failures.record_failure(file_obj, processor.name, exc)
        except Exception:
            logger.warning(
                "Could not record the failed %s attempt for %s",
                processor.name,
                scrub(file_obj.uuid),
                exc_info=True,
            )
        return False
    if stored:
        try:
            processing_failures.clear_failure(file_obj, processor.name)
        except Exception:
            logger.warning(
                "Could not clear the %s failure row for %s",
                processor.name,
                scrub(file_obj.uuid),
                exc_info=True,
            )
    return bool(stored)


def run_pipeline(file_uuid):
    """Run every upload processor on one file, then mark it ready.

    The file's ``processing_status`` only moves while the row still holds the
    bytes the run started from: a replacement that lands mid-run set it back to
    pending and queued a pipeline of its own, which is the one that settles it.

    Returns the status the file was left in, or None when there was nothing to
    run on.
    """
    try:
        file_obj = File.objects.select_related("owner").get(uuid=file_uuid)
    except File.DoesNotExist, ValidationError, ValueError, TypeError:
        return None
    if file_obj.node_type != File.NodeType.FILE:
        return None
    if file_obj.deleted_at is not None:
        # Trashed before we ran. The status stays where it is: once restored,
        # the catch-up finds the file stalled and runs the pipeline then.
        return None

    Status = File.ProcessingStatus
    content_hash = file_obj.content_hash
    this_content = File.objects.filter(pk=file_obj.pk, content_hash=content_hash)
    this_content.update(processing_status=Status.PROCESSING)

    for processor in registered_processors():
        if processor.applies_to is None or not processor.enabled():
            continue
        # Reloaded before each step: the scan's verdict, a flag a previous
        # processor set, or a replacement of the content all have to be seen
        # by the next one.
        try:
            file_obj.refresh_from_db()
        except File.DoesNotExist:
            return None
        if file_obj.content_hash != content_hash or file_obj.deleted_at is not None:
            return None
        _run_step(processor, file_obj)

    settled = this_content.filter(processing_status=Status.PROCESSING).update(
        processing_status=Status.READY
    )
    return Status.READY if settled else None


def _run_step(processor, file_obj):
    try:
        applies = processor.applies_to(file_obj)
    except Exception:
        logger.exception(
            "Processor %s could not decide on %s", processor.name, scrub(file_obj.uuid)
        )
        return
    if applies:
        if processor.enqueue is None:
            run_processor(processor, file_obj)
            return
        try:
            processor.enqueue(file_obj)
        except Exception:
            # The catch-up queues it again on its next pass.
            logger.exception(
                "Could not queue %s for %s", processor.name, scrub(file_obj.uuid)
            )
    elif processor.forget is not None:
        try:
            processor.forget(file_obj)
        except Exception:
            logger.exception(
                "Processor %s could not forget %s",
                processor.name,
                scrub(file_obj.uuid),
            )


def stalled_files_qs():
    """Live files whose pipeline should have settled by now and has not."""
    return File.objects.alive().filter(
        node_type=File.NodeType.FILE,
        processing_status__in=[
            File.ProcessingStatus.PENDING,
            File.ProcessingStatus.PROCESSING,
        ],
        updated_at__lt=timezone.now() - STALLED_AFTER,
    )


def queue_stalled(*, limit=None, expires=None):
    """Queue the pipeline again for each stalled file; return how many."""
    from ..tasks import process_file

    queued = 0
    for uuid in itertools.islice(_keyset(stalled_files_qs()), limit):
        process_file.apply_async(args=[str(uuid)], expires=expires)
        queued += 1
    return queued


def pending_ids(processor, *, reanalyze=False, limit=None, start_after=None):
    """Yield the uuids of *processor*'s pending files in uuid order.

    From just after *start_after* when given, wrapping around to the start
    once the end is reached, so every pending file comes up in turn.
    """
    queryset = processor.pending_files(reanalyze=reanalyze)
    ids = _keyset(queryset, after=start_after)
    if start_after is not None:
        ids = itertools.chain(ids, _keyset(queryset, until=start_after))
    yield from itertools.islice(ids, limit)


def _keyset(queryset, *, after=None, until=None):
    last = after
    while True:
        page_qs = queryset.order_by("uuid")
        if last is not None:
            page_qs = page_qs.filter(uuid__gt=last)
        if until is not None:
            page_qs = page_qs.filter(uuid__lte=until)
        page = list(page_qs.values_list("uuid", flat=True)[:_PAGE_SIZE])
        if not page:
            return
        yield from page
        last = page[-1]


def queue_pending(
    processor, *, reanalyze=False, limit=None, expires=None, resume=False
):
    """Queue one files.catch_up_file task per pending file; return how many.

    With *resume*, start after the last file the previous resumed call queued
    for this processor (see _CURSOR_KEY).
    """
    cursor_key = _CURSOR_KEY.format(processor.name)
    start_after = parse_uuid_or_none(cache.get(cursor_key)) if resume else None
    queued = 0
    last_uuid = None
    for uuid in pending_ids(
        processor, reanalyze=reanalyze, limit=limit, start_after=start_after
    ):
        _queue(processor, uuid, reanalyze=reanalyze, expires=expires)
        queued += 1
        last_uuid = uuid
    if resume:
        if last_uuid is None:
            cache.delete(cursor_key)
        else:
            cache.set(cursor_key, str(last_uuid), timeout=None)
    return queued


def queue_files(processor, file_ids):
    """Queue one files.catch_up_file task per file in *file_ids*; return how many.

    For files someone asked for by name: they are queued whatever their place
    in the backlog, which a bounded pass does not promise. A file that is no
    longer pending by the time its task runs is skipped there.
    """
    file_ids = list(file_ids)
    for uuid in file_ids:
        _queue(processor, uuid, reanalyze=False, expires=None)
    return len(file_ids)


def _queue(processor, uuid, *, reanalyze, expires):
    from ..tasks import catch_up_file

    catch_up_file.apply_async(
        args=[processor.name, str(uuid)],
        kwargs={"reanalyze": reanalyze},
        expires=expires,
    )


@on_file_event(FileEvent.Action.CREATED, FileEvent.Action.CONTENT_REPLACED)
def queue_pipeline_for_event(event):
    """Queue the upload pipeline for a file whose content just landed."""
    from ..tasks import process_file

    file_obj = event.file
    if file_obj.node_type != File.NodeType.FILE or file_obj.deleted_at is not None:
        return
    process_file.delay(str(file_obj.pk))
