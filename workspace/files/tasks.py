"""Celery tasks for file synchronization and maintenance."""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

logger = logging.getLogger(__name__)
User = get_user_model()


@shared_task(name='files.sync_all_users', bind=True, max_retries=0)
def sync_all_users(self):
    """Full recursive sync for all active users. Scheduled via Celery Beat."""
    from workspace.files.sync import FileSyncService

    service = FileSyncService(log=logger)
    total = {
        'users_processed': 0,
        'files_created': 0,
        'folders_created': 0,
        'files_soft_deleted': 0,
        'folders_soft_deleted': 0,
        'errors': [],
    }

    for user in User.objects.filter(is_active=True).iterator():
        logger.info("Syncing files for user: %s", user.username)
        result = service.sync_user_recursive(user)
        total['users_processed'] += 1
        total['files_created'] += result.files_created
        total['folders_created'] += result.folders_created
        total['files_soft_deleted'] += result.files_soft_deleted
        total['folders_soft_deleted'] += result.folders_soft_deleted
        total['errors'].extend(result.errors)

    logger.info("Full sync complete: %s", total)
    return total


@shared_task(name='files.purge_trash', bind=True, max_retries=0)
def purge_trash(self):
    """Hard-delete files that have been in trash longer than TRASH_RETENTION_DAYS."""
    from workspace.files.models import File

    retention_days = getattr(settings, 'TRASH_RETENTION_DAYS', 30)
    cutoff = timezone.now() - timedelta(days=retention_days)

    qs = File.objects.filter(deleted_at__lte=cutoff)
    files_count = qs.filter(node_type=File.NodeType.FILE).count()
    folders_count = qs.filter(node_type=File.NodeType.FOLDER).count()

    if not (files_count + folders_count):
        logger.info("Trash purge: nothing to delete.")
        return {'files_deleted': 0, 'folders_deleted': 0, 'retention_days': retention_days}

    logger.info(
        "Trash purge: deleting %d files and %d folders older than %d days",
        files_count, folders_count, retention_days,
    )
    qs.delete()

    logger.info("Trash purge complete.")
    return {
        'files_deleted': files_count,
        'folders_deleted': folders_count,
        'retention_days': retention_days,
    }


@shared_task(name='files.generate_thumbnails', bind=True, max_retries=0)
def generate_thumbnails(self):
    """Generate thumbnails for image files that don't have one yet."""
    from workspace.files.services.thumbnails import generate_missing_thumbnails

    logger.info("Starting thumbnail generation...")
    stats = generate_missing_thumbnails()
    logger.info("Thumbnail generation complete: %s", stats)
    return stats


@shared_task(name='files.sync_folder', bind=True, max_retries=0)
def sync_folder(self, user_id, folder_uuid=None):
    """Shallow sync for a single folder. Can be triggered via API."""
    from workspace.files.models import File
    from workspace.files.sync import FileSyncService

    user = User.objects.get(pk=user_id)
    parent_db = None

    if folder_uuid:
        parent_db = File.objects.get(
            uuid=folder_uuid,
            owner=user,
            node_type=File.NodeType.FOLDER,
            deleted_at__isnull=True,
        )

    service = FileSyncService(log=logger)
    result = service.sync_folder_shallow(user, parent_db)
    return {
        'files_created': result.files_created,
        'folders_created': result.folders_created,
        'files_soft_deleted': result.files_soft_deleted,
        'folders_soft_deleted': result.folders_soft_deleted,
        'errors': result.errors,
    }
