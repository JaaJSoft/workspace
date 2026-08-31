"""Celery tasks for file synchronization and maintenance."""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.utils import timezone

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)
User = get_user_model()


# Derived from the beat interval rather than fixed, so tuning
# FILES_SYNC_INTERVAL cannot leave the TTL shorter than the period: a lock
# that expires mid-walk lets the next tick start a second concurrent walk
# for the same user, and two walks racing phase 1 can both decide the same
# disk entry is missing and create a row for it. The floor keeps the
# guard meaningful under a very short configured interval; a healthy run
# releases the lock on exit, so the TTL only bounds recovery after a
# worker is killed outright.
SYNC_USER_LOCK_TTL = max(int(2 * getattr(settings, "FILES_SYNC_INTERVAL", 1800)), 1800)

# Rows fetched per page when indexing a subtree (see index_search_document).
_INDEX_PAGE_SIZE = 500


@shared_task(name="files.sync_all_users", bind=True, max_retries=0)
def sync_all_users(self):
    """Dispatch a per-user sync task for every active user.

    Fanning out (rather than walking every user in this task) lets the
    walks run in parallel across workers, keeps one user's huge tree or
    unreadable mount from delaying everyone behind it, and confines a
    failure to the user that caused it.
    """
    dispatched = 0
    failed = 0

    user_ids = User.objects.filter(is_active=True).values_list("pk", flat=True)
    for user_id in user_ids.iterator():
        try:
            sync_user_files.delay(user_id)
            dispatched += 1
        except Exception:
            # Broker refusal for one user must not abort the whole fan-out.
            logger.exception("Failed to enqueue file sync for user %s", user_id)
            failed += 1

    logger.info(
        "File sync dispatched: %d users, %d failed to enqueue", dispatched, failed
    )
    return {"users_dispatched": dispatched, "enqueue_failures": failed}


@shared_task(name="files.sync_user_files", bind=True, max_retries=0)
def sync_user_files(self, user_id):
    """Full recursive disk <-> DB sync for a single user.

    Guarded by an advisory lock: successive beat ticks would otherwise
    stack redundant walks for the same user whenever one run outlives the
    schedule period.
    """
    from workspace.common.task_locks import task_lock
    from workspace.files.sync import FileSyncService

    try:
        user = User.objects.get(pk=user_id, is_active=True)
    except User.DoesNotExist:
        logger.warning("Sync skipped: user %s not found or inactive", user_id)
        return {"status": "not_found"}

    with task_lock(f"files:sync:user:{user_id}", SYNC_USER_LOCK_TTL) as acquired:
        if not acquired:
            logger.info(
                "Sync already running for user %s, skipping", scrub(user.username)
            )
            return {"status": "skipped", "reason": "already_running"}

        logger.info("Syncing files for user: %s", scrub(user.username))
        result = FileSyncService(log=logger).sync_user_recursive(user)

    return {
        "status": "ok",
        "files_created": result.files_created,
        "folders_created": result.folders_created,
        "files_soft_deleted": result.files_soft_deleted,
        "folders_soft_deleted": result.folders_soft_deleted,
        "errors": result.errors,
    }


@shared_task(name="files.purge_trash", bind=True, max_retries=0)
def purge_trash(self):
    """Hard-delete files that have been in trash longer than TRASH_RETENTION_DAYS."""
    from workspace.files.models import File

    retention_days = getattr(settings, "TRASH_RETENTION_DAYS", 30)
    cutoff = timezone.now() - timedelta(days=retention_days)

    qs = File.objects.filter(deleted_at__lte=cutoff)
    # Both breakdowns in one pass. delete()'s own per-model total can't
    # substitute here: it lumps files and folders together and is inflated
    # by cascade deletions (tags, shares, child files, ...).
    counts = qs.aggregate(
        files=Count("pk", filter=Q(node_type=File.NodeType.FILE)),
        folders=Count("pk", filter=Q(node_type=File.NodeType.FOLDER)),
    )
    files_count = counts["files"]
    folders_count = counts["folders"]

    if not (files_count + folders_count):
        logger.info("Trash purge: nothing to delete.")
        return {
            "files_deleted": 0,
            "folders_deleted": 0,
            "retention_days": retention_days,
        }

    logger.info(
        "Trash purge: deleting %d files and %d folders older than %d days",
        files_count,
        folders_count,
        retention_days,
    )
    # select_related('owner') avoids N+1 in the pre_delete signal,
    # which reads instance.owner.username for each File.
    qs.select_related("owner").delete()

    logger.info("Trash purge complete.")
    return {
        "files_deleted": files_count,
        "folders_deleted": folders_count,
        "retention_days": retention_days,
    }


@shared_task(name="files.generate_thumbnails", bind=True, max_retries=0)
def generate_thumbnails(self, retry_failed=False):
    """Generate thumbnails for image files that don't have one yet."""
    from workspace.files.services.thumbnails.generation import (
        generate_missing_thumbnails,
    )

    logger.info("Starting thumbnail generation (retry_failed=%s)...", retry_failed)
    stats = generate_missing_thumbnails(retry_failed=retry_failed)
    logger.info("Thumbnail generation complete: %s", stats)
    return stats


@shared_task(name="files.sync_folder", bind=True, max_retries=0)
def sync_folder(self, user_id, folder_uuid=None):
    """Shallow sync for a single folder. Can be triggered via API."""
    from workspace.files.models import File
    from workspace.files.sync import FileSyncService

    user = User.objects.get(pk=user_id)
    parent_db = None

    if folder_uuid:
        from workspace.files.services import FileService

        parent_db = FileService.user_files_qs(user).get(
            uuid=folder_uuid,
            node_type=File.NodeType.FOLDER,
        )

    service = FileSyncService(log=logger)
    result = service.sync_folder_shallow(user, parent_db)
    return {
        "files_created": result.files_created,
        "folders_created": result.folders_created,
        "files_soft_deleted": result.files_soft_deleted,
        "folders_soft_deleted": result.folders_soft_deleted,
        "errors": result.errors,
    }


@shared_task(name="files.run_file_event_handlers", bind=True, max_retries=0)
def run_file_event_handlers(self, event_uuid):
    """Run the registered handlers for a recorded FileEvent (off-request)."""
    from workspace.files.services.event_dispatch import run_handlers

    run_handlers(event_uuid)


@shared_task(name="files.index_search_document", bind=True, max_retries=0)
def index_search_document(self, file_uuid, include_descendants=False):
    """Extract *file_uuid*'s text and write its full-text search document.

    max_retries=0 on purpose: an extractor that cannot read a blob will not
    read it on the next attempt either, and a permanently unindexable file
    must never turn into a retry loop. The file stays findable by name once
    the backfill command runs.
    """
    from django.core.exceptions import ValidationError

    from workspace.files.models import File
    from workspace.files.services.search_index import (
        build_documents,
        index_file,
        write_documents,
    )

    try:
        file_obj = File.objects.get(uuid=file_uuid)
    except File.DoesNotExist, ValidationError, ValueError, TypeError:
        # Hard-deleted between the event and this task, or a malformed id.
        return {"status": "not_found"}

    indexed = 1 if index_file(file_obj) else 0
    skipped = 0 if indexed else 1

    if include_descendants and file_obj.node_type == File.NodeType.FOLDER:
        # A copied folder records a single CREATED event for its root, so the
        # duplicated subtree would otherwise never be indexed. Paged by
        # keyset rather than streamed: a read cursor held open across the
        # write transactions below is what makes SQLite raise "database is
        # locked" (see reindex_files_search for the full reason).
        descendants = File.objects.filter(path__startswith=f"{file_obj.path}/")
        last_uuid = None
        while True:
            page_qs = descendants.order_by("uuid")
            if last_uuid is not None:
                page_qs = page_qs.filter(uuid__gt=last_uuid)
            page = list(page_qs[:_INDEX_PAGE_SIZE])
            if not page:
                break
            last_uuid = page[-1].uuid
            batch = build_documents(page)
            written = write_documents(batch)
            indexed += written
            skipped += len(page) - written

    return {"status": "ok", "indexed": indexed, "failed": skipped}


@shared_task(name="files.scan_file", bind=True, max_retries=0)
def scan_file(self, file_uuid):
    """Scan one file's content for malware and record the verdict.

    max_retries=0 on purpose: a daemon that is down will be down on the next
    attempt too, and a permanently unscannable file must never turn into a
    retry loop. The scan_files management command is the recovery path.
    """
    import time

    from django.core.exceptions import ValidationError

    from workspace.files.metrics import (
        FILES_MALWARE_SCAN_DURATION,
        FILES_MALWARE_SCAN_RESULT,
    )
    from workspace.files.models import File, FileScan
    from workspace.files.services.scanning.base import ScanVerdict
    from workspace.files.services.scanning.capped import CappedReader
    from workspace.files.services.scanning.policy import blocked_statuses
    from workspace.files.services.scanning.registry import get_scanner
    from workspace.files.services.search_index import unindex_file

    scanner = get_scanner()
    if scanner is None:
        return {"status": "disabled"}

    try:
        file_obj = File.objects.get(uuid=file_uuid)
    except File.DoesNotExist, ValidationError, ValueError, TypeError:
        # Hard-deleted between the event and this task, or a malformed id.
        return {"status": "not_found"}

    if file_obj.node_type != File.NodeType.FILE or not file_obj.content:
        return {"status": "not_applicable"}

    # Identifies the bytes this run is about to look at. Re-read just before
    # the verdict is written, it is what tells a slow scan of the old content
    # apart from a verdict about what the row holds now.
    scanned_hash = file_obj.content_hash

    max_bytes = int(getattr(settings, "FILES_MALWARE_SCAN_MAX_BYTES", 25 * 1024 * 1024))

    if (file_obj.size or 0) > max_bytes:
        verdict = ScanVerdict(
            status=FileScan.Status.SKIPPED, detail="larger than the scan size cap"
        )
    else:
        started = time.monotonic()
        try:
            handle = file_obj.content.open("rb")
        except (FileNotFoundError, OSError) as exc:
            logger.warning(
                "Malware scan cannot read blob for %s: %s",
                scrub(file_obj.content.name),
                scrub(str(exc)),
            )
            verdict = ScanVerdict(status=FileScan.Status.ERROR, detail=str(exc)[:500])
        else:
            try:
                reader = CappedReader(handle, max_bytes)
                verdict = scanner.scan(reader, name=file_obj.name)
                if reader.truncated and verdict.status == FileScan.Status.CLEAN:
                    # Clean as far as we looked is not the same as clean.
                    verdict = ScanVerdict(
                        status=FileScan.Status.SKIPPED,
                        detail="larger than the scan size cap",
                    )
            finally:
                handle.close()
            FILES_MALWARE_SCAN_DURATION.observe(time.monotonic() - started)

    # The content may have been replaced while this scan was running: a large
    # infected upload takes longer to scan than the clean file that replaced
    # it, so the two verdicts can land out of order. Writing the older one
    # would quarantine content it never read, and permanently - max_retries=0
    # and scan_files without --rescan both leave an existing row alone.
    current_hash = (
        File.objects.filter(pk=file_obj.pk)
        .values_list("content_hash", flat=True)
        .first()
    )
    if current_hash is None:
        # Hard-deleted mid-scan; the FK would fail anyway.
        return {"status": "not_found"}
    if current_hash != scanned_hash:
        logger.info(
            "Discarding a stale malware verdict for %s: its content changed mid-scan",
            scrub(file_obj.name),
        )
        return {"status": "stale"}

    blocked = blocked_statuses()
    # Read before the write: only a blocked -> readable transition has a
    # document to restore. Re-indexing every clean verdict would extract the
    # same text a second time for the same upload and put this task in a race
    # with the indexing one over a single FTS row.
    was_blocked = (
        FileScan.objects.filter(file=file_obj).values_list("status", flat=True).first()
        in blocked
    )

    FileScan.objects.update_or_create(
        file=file_obj,
        defaults={
            "status": verdict.status,
            "signature": verdict.signature,
            "detail": verdict.detail,
            # The hash captured before the blob was opened, not a fresh read:
            # it is the one the verdict actually describes, and the staleness
            # check above has just confirmed the row still holds it.
            "content_hash": scanned_hash,
            "scanned_at": timezone.now(),
        },
    )
    FILES_MALWARE_SCAN_RESULT.labels(result=verdict.status).inc()

    if verdict.status in blocked:
        unindex_file(file_obj)
        if file_obj.has_thumbnail:
            from workspace.files.services.thumbnails.generation import (
                delete_thumbnail,
            )

            delete_thumbnail(file_obj.uuid)
            file_obj.has_thumbnail = False
            file_obj.save(update_fields=["has_thumbnail"])
    elif was_blocked:
        from workspace.files.services.scanning.override import restore_after_unblock

        restore_after_unblock(file_obj)

    return {"status": verdict.status, "signature": verdict.signature}
