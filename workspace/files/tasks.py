"""Celery tasks for file synchronization and maintenance."""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, F, Q
from django.utils import timezone

from workspace.common.logging import scrub
from workspace.common.task_priority import (
    BACKGROUND_PRIORITY,
    LOW_PRIORITY,
    NORMAL_PRIORITY,
)

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


@shared_task(
    name="files.sync_all_users", priority=LOW_PRIORITY, bind=True, max_retries=0
)
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


@shared_task(
    name="files.sync_user_files", priority=LOW_PRIORITY, bind=True, max_retries=0
)
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


@shared_task(
    name="files.purge_trash", priority=BACKGROUND_PRIORITY, bind=True, max_retries=0
)
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


# Each pass queues one catch_up_file task per pending file of every
# registered reader (services/catch_up.py), so the work spreads over every
# worker. The bound, per reader, caps what one pass puts on the broker; a
# larger backlog drains over the following passes.
CATCH_UP_LIMIT = 20_000

# The tasks of a pass expire at this share of FILES_CATCH_UP_INTERVAL, counted
# from the start of the pass, so each one has run or been dropped by the time
# the next pass queues the files still pending: a file that keeps failing is
# attempted once per pass, not twice. The worker checks the expiry when it
# pops a message, so an expired one stays in Redis until then - at most one
# pass's worth.
CATCH_UP_EXPIRY_SHARE = 0.9


@shared_task(
    name="files.catch_up", priority=BACKGROUND_PRIORITY, bind=True, max_retries=0
)
def catch_up(self, names=None):
    """Queue the pending files of every reader, or of the readers in *names*.

    Returns how many files were queued per reader.
    """
    from workspace.files.services.catch_up import queue_pending, resolve

    readers, unknown = resolve(names)
    if unknown:
        logger.warning("Catch-up skips unknown reader(s): %s", ", ".join(unknown))
    expires = timezone.now() + timedelta(
        seconds=settings.FILES_CATCH_UP_INTERVAL * CATCH_UP_EXPIRY_SHARE
    )
    stats = {
        reader.name: queue_pending(
            reader, limit=CATCH_UP_LIMIT, expires=expires, resume=True
        )
        for reader in readers
    }
    logger.info("Catch-up queued %s", stats)
    return stats


@shared_task(
    name="files.catch_up_file",
    priority=BACKGROUND_PRIORITY,
    bind=True,
    max_retries=0,
    ignore_result=True,
)
def catch_up_file(self, name, file_uuid, reanalyze=False):
    """Run reader *name* on one file.

    Unless *reanalyze*, a file that is no longer pending is skipped without
    reading its blob: its upload event may have processed it while this task
    waited in the queue.

    max_retries=0 on purpose: a blob that cannot be read now will not be read
    on a retry a few seconds later either, and the hourly catch-up already
    comes back for it.
    """
    from django.core.exceptions import ValidationError

    from workspace.files.models import File
    from workspace.files.services.catch_up import get_catch_up

    reader = get_catch_up(name)
    if reader is None:
        return {"status": "skipped"}
    try:
        file_obj = (
            reader.pending_files(reanalyze=reanalyze)
            .select_related("owner")
            .get(uuid=file_uuid)
        )
    except File.DoesNotExist, ValidationError, ValueError, TypeError:
        return {"status": "skipped"}
    return {"status": "ok" if reader.process(file_obj) else "skipped"}


@shared_task(
    name="files.sync_folder", priority=NORMAL_PRIORITY, bind=True, max_retries=0
)
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


@shared_task(
    name="files.run_file_event_handlers",
    priority=NORMAL_PRIORITY,
    bind=True,
    max_retries=0,
)
def run_file_event_handlers(self, event_uuid):
    """Run the registered handlers for a recorded FileEvent (off-request)."""
    from workspace.files.services.event_dispatch import run_handlers

    run_handlers(event_uuid)


@shared_task(
    name="files.index_search_document",
    priority=NORMAL_PRIORITY,
    bind=True,
    max_retries=0,
)
def index_search_document(self, file_uuid, include_descendants=False):
    """Extract *file_uuid*'s text and write its full-text search document.

    max_retries=0 on purpose: an extractor that cannot read a blob will not
    read it on the next attempt either, and a permanently unindexable file
    must never turn into a retry loop. The hourly catch-up comes back for a
    file whose document is missing or stale.
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
        # locked" (see services/catch_up.py).
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


@shared_task(name="files.scan_file", priority=NORMAL_PRIORITY, bind=True, max_retries=0)
def scan_file(self, file_uuid):
    """Scan one file's content for malware and record the verdict.

    max_retries=0 on purpose: a daemon that is down will be down on the next
    attempt too, and a permanently unscannable file must never turn into a
    retry loop. The hourly catch-up comes back for a file left without a
    current verdict.
    """
    from django.core.exceptions import ValidationError

    from workspace.files.models import File
    from workspace.files.services.scanning.registry import get_scanner
    from workspace.files.services.scanning.scan import scan_and_record

    scanner = get_scanner()
    if scanner is None:
        return {"status": "disabled"}

    try:
        file_obj = File.objects.get(uuid=file_uuid)
    except File.DoesNotExist, ValidationError, ValueError, TypeError:
        # Hard-deleted between the event and this task, or a malformed id.
        return {"status": "not_found"}
    return scan_and_record(file_obj, scanner)


@shared_task(name="files.notify_share_link_uploads", priority=NORMAL_PRIORITY)
def notify_share_link_uploads(link_uuid):
    """Tell the link owner about the uploads that landed since the last run."""
    from django.core.cache import cache

    from workspace.files.models import FileShareLink
    from workspace.files.services.public_links import upload_notification_cache_key
    from workspace.notifications.services.notifications import notify

    link = (
        FileShareLink.objects.select_related("file", "created_by")
        .filter(uuid=link_uuid)
        .first()
    )
    if link is None:
        cache.delete(upload_notification_cache_key(link_uuid))
        return

    delta = link.upload_count - link.notified_upload_count
    if delta > 0:
        subject = "1 file was" if delta == 1 else f"{delta} files were"
        notify(
            recipient=link.created_by,
            origin="files",
            title=f'{subject} added to "{link.file.name}"',
            body="Uploaded through your share link.",
            url=f"/files/{link.file.uuid}",
            source=link.file,
        )
        FileShareLink.objects.filter(pk=link.pk).update(
            notified_upload_count=link.upload_count
        )

    # Last, so the next upload starts a fresh window.
    cache.delete(upload_notification_cache_key(link_uuid))

    # An upload that landed while this task ran advanced upload_count after the
    # read above, and the update wrote back the value we read, so its delta
    # survives. It is only reported if another upload follows and wins a fresh
    # election - so if it was the last of the burst, elect the follow-up here.
    from workspace.files.services.public_links import schedule_upload_notification

    if (
        FileShareLink.objects.filter(pk=link.pk)
        .exclude(notified_upload_count=F("upload_count"))
        .exists()
    ):
        schedule_upload_notification(link)
