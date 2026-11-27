"""Celery tasks for database and storage maintenance."""

import logging
import os
import time

from celery import shared_task
from django.conf import settings

from workspace.common.task_priority import BACKGROUND_PRIORITY, LOW_PRIORITY
from workspace.core.services.sqlite_maintenance import is_sqlite, run_maintenance

logger = logging.getLogger(__name__)


# LOW rather than BACKGROUND: behind a catch-up backlog the nightly pass would
# start once the backlog drains, possibly in office hours, and every step of
# its incremental vacuum takes the write lock.
@shared_task(
    name="core.db_maintenance", priority=LOW_PRIORITY, bind=True, max_retries=0
)
def db_maintenance(self, integrity_check=False):
    """Run the SQLite pass: optimize, incremental vacuum, WAL checkpoint.

    The weekly beat entry passes integrity_check=True. A full VACUUM is never
    scheduled: see ``core.services.sqlite_maintenance``.
    """
    if not is_sqlite():
        logger.info("Database backend is not SQLite - skipping maintenance.")
        return {"skipped": True, "reason": "not SQLite"}

    t0 = time.monotonic()
    result = run_maintenance(with_integrity_check=integrity_check)
    result["total_ms"] = round((time.monotonic() - t0) * 1000, 1)
    checkpoint = result["wal_checkpoint"]
    logger.info(
        "SQLite maintenance finished in %s ms (freed %s pages in %s steps, "
        "%s free pages left, size %s -> %s bytes, WAL %s/%s pages copied)",
        result["total_ms"],
        result["pages_freed"],
        result["steps"],
        result["freelist_count"],
        result["size_before"],
        result["size_after"],
        checkpoint["pages_checkpointed"],
        checkpoint["pages_written"],
    )
    if not checkpoint["complete"]:
        logger.warning(
            "SQLite WAL checkpoint left pages behind for a reader still open: "
            "the file shrinks at a later checkpoint"
        )
    return result


# Every write refreshes an upload's mtime, so a spool file untouched for this
# long belongs to no request any more.
STALE_UPLOAD_SECONDS = 24 * 3600


@shared_task(name="core.purge_stale_uploads", priority=BACKGROUND_PRIORITY)
def purge_stale_uploads():
    """Delete the upload spool files a killed web worker left behind.

    A request removes its spool file when it ends, but a worker killed
    mid-upload (a deploy, the OOM killer) never gets there, and
    ``FILE_UPLOAD_TEMP_DIR`` sits on the persistent volume, which no restart
    empties. Only Django's own ``tmp*.upload*`` names are touched, in case the
    directory is shared with something else.
    """
    removed = 0
    cutoff = time.time() - STALE_UPLOAD_SECONDS
    try:
        entries = list(os.scandir(settings.FILE_UPLOAD_TEMP_DIR))
    except FileNotFoundError:
        return removed
    for entry in entries:
        if not (entry.name.startswith("tmp") and ".upload" in entry.name):
            continue
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
            if entry.stat(follow_symlinks=False).st_mtime >= cutoff:
                continue
            os.remove(entry.path)
        except FileNotFoundError:
            continue  # its request finished in the meantime
        removed += 1
    if removed:
        logger.info("Removed %d stale upload spool files", removed)
    return removed
