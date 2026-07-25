"""Celery tasks for file synchronization and maintenance."""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
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
    files_count = qs.filter(node_type=File.NodeType.FILE).count()
    folders_count = qs.filter(node_type=File.NodeType.FOLDER).count()

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
def generate_thumbnails(self):
    """Generate thumbnails for image files that don't have one yet."""
    from workspace.files.services.thumbnails import generate_missing_thumbnails

    logger.info("Starting thumbnail generation...")
    stats = generate_missing_thumbnails()
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
