"""Celery tasks for file synchronization."""

import logging

from celery import shared_task
from django.contrib.auth import get_user_model

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
