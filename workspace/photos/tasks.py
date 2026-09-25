"""Celery tasks for the photo library."""

import logging

from celery import shared_task

from workspace.common.task_priority import BACKGROUND_PRIORITY

logger = logging.getLogger(__name__)

# The hourly pass catches the uploads whose event dispatch was lost, and the
# rows a new reader version marks as pending again (ANALYSIS_VERSIONS in
# services/analysis.py), which on a large library is a whole backfill. It
# queues one analyze_photo task per file, so the reading spreads over every
# worker, at BACKGROUND_PRIORITY so the backlog never delays other tasks. The
# bound caps what one pass puts on the broker; a larger backlog drains over
# the following passes.
CATCH_UP_LIMIT = 20_000

# One beat interval (the analyze-photos entry of CELERY_BEAT_SCHEDULE). A task
# no worker reached by the next pass is dropped, and that pass queues the file
# again, so a backlog larger than the workers' throughput never piles up.
CATCH_UP_EXPIRES = 3600.0


@shared_task(name="photos.analyze_pending", bind=True, max_retries=0)
def analyze_pending(self):
    """Catch-up pass: queue the analysis of photos and videos missing or stale in the library."""
    from workspace.photos.services.analysis import pending_media_ids

    queued = 0
    for uuid in pending_media_ids(limit=CATCH_UP_LIMIT):
        analyze_photo.apply_async(
            args=[str(uuid)], priority=BACKGROUND_PRIORITY, expires=CATCH_UP_EXPIRES
        )
        queued += 1
    logger.info("Media analysis catch-up queued %d file(s)", queued)
    return {"queued": queued}


@shared_task(name="photos.analyze_photo", bind=True, max_retries=0, ignore_result=True)
def analyze_photo(self, file_uuid, reanalyze=False):
    """Analyze one file, queued by the catch-up pass or the analyze_photos command.

    Unless *reanalyze*, a file that is no longer pending is skipped without
    reading its blob: its upload event may have analyzed it while this task
    waited in the queue.

    max_retries=0 on purpose: a blob that cannot be read now will not be read
    on a retry a few seconds later either, and the hourly catch-up already
    comes back for it.
    """
    from django.core.exceptions import ValidationError

    from workspace.files.models import File
    from workspace.photos.services.analysis import analyze_media, pending_media_qs

    try:
        file_obj = File.objects.select_related("owner").get(uuid=file_uuid)
    except File.DoesNotExist, ValidationError, ValueError, TypeError:
        return {"status": "not_found"}
    if not reanalyze and not pending_media_qs().filter(pk=file_obj.pk).exists():
        return {"status": "skipped"}
    return {"status": "ok" if analyze_media(file_obj) is not None else "skipped"}
