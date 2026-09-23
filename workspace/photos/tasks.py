"""Celery tasks for the photo library."""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="photos.analyze_pending", bind=True, max_retries=0)
def analyze_pending(self):
    """Catch-up pass: analyze every raster image missing or stale in the library."""
    from workspace.photos.services.analysis import analyze_pending as run

    stats = run()
    logger.info("Photo analysis catch-up complete: %s", stats)
    return stats


@shared_task(name="photos.analyze_photo", bind=True, max_retries=0)
def analyze_photo(self, file_uuid):
    """Analyze one file, queued by the analyze_photos command.

    max_retries=0 on purpose: a blob that cannot be read now will not be read
    on a retry a few seconds later either, and the hourly catch-up already
    comes back for it.
    """
    from django.core.exceptions import ValidationError

    from workspace.files.models import File
    from workspace.photos.services.analysis import analyze_photo as run

    try:
        file_obj = File.objects.select_related("owner").get(uuid=file_uuid)
    except File.DoesNotExist, ValidationError, ValueError, TypeError:
        return {"status": "not_found"}
    return {"status": "ok" if run(file_obj) is not None else "skipped"}
