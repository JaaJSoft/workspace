"""Celery tasks of the core app: database maintenance and the instance's own mail."""

import logging
import os
import time
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import connection
from django.utils import timezone

from workspace.common.task_priority import (
    BACKGROUND_PRIORITY,
    LOW_PRIORITY,
    NORMAL_PRIORITY,
)

logger = logging.getLogger(__name__)


def _is_sqlite():
    return connection.vendor == "sqlite"


def _db_path():
    return connection.settings_dict["NAME"]


def _run_maintenance(skip_vacuum=False, skip_integrity_check=False):
    """Run SQLite maintenance operations. Returns a result dict."""
    if not _is_sqlite():
        msg = "Database backend is not SQLite - skipping maintenance."
        logger.info(msg)
        return {"skipped": True, "reason": msg}

    db_path = _db_path()
    result = {}

    # --- 1. PRAGMA optimize ---
    t0 = time.monotonic()
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA optimize;")
    result["optimize_ms"] = round((time.monotonic() - t0) * 1000, 1)
    logger.info("PRAGMA optimize completed in %s ms", result["optimize_ms"])

    # --- 2. WAL checkpoint (TRUNCATE) ---
    t0 = time.monotonic()
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        row = cursor.fetchone()
    result["wal_checkpoint"] = {
        "return_code": row[0],
        "pages_written": row[1],
        "pages_checkpointed": row[2],
    }
    result["wal_checkpoint_ms"] = round((time.monotonic() - t0) * 1000, 1)
    logger.info(
        "WAL checkpoint completed in %s ms (code=%s, written=%s, checkpointed=%s)",
        result["wal_checkpoint_ms"],
        row[0],
        row[1],
        row[2],
    )

    # --- 3. VACUUM ---
    if skip_vacuum:
        result["vacuum_skipped"] = True
        logger.info("VACUUM skipped (--skip-vacuum)")
    else:
        try:
            size_before = os.path.getsize(db_path)
        except OSError:
            size_before = None

        t0 = time.monotonic()
        with connection.cursor() as cursor:
            cursor.execute("VACUUM;")
        result["vacuum_ms"] = round((time.monotonic() - t0) * 1000, 1)

        try:
            size_after = os.path.getsize(db_path)
        except OSError:
            size_after = None

        result["size_before"] = size_before
        result["size_after"] = size_after
        if size_before is not None and size_after is not None:
            saved = size_before - size_after
            result["size_saved"] = saved
            logger.info(
                "VACUUM completed in %s ms (before=%s bytes, after=%s bytes, saved=%s bytes)",
                result["vacuum_ms"],
                size_before,
                size_after,
                saved,
            )
        else:
            logger.info("VACUUM completed in %s ms", result["vacuum_ms"])

    # --- 4. Integrity check ---
    if skip_integrity_check:
        result["integrity_check_skipped"] = True
        logger.info("Integrity check skipped (--skip-integrity-check)")
    else:
        t0 = time.monotonic()
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA integrity_check;")
            rows = cursor.fetchall()
        result["integrity_check_ms"] = round((time.monotonic() - t0) * 1000, 1)
        check_result = rows[0][0] if rows else "unknown"
        result["integrity_check"] = check_result

        if check_result == "ok":
            logger.info(
                "Integrity check passed in %s ms",
                result["integrity_check_ms"],
            )
        else:
            logger.warning(
                "Integrity check reported issues in %s ms: %s",
                result["integrity_check_ms"],
                check_result,
            )

    return result


# LOW rather than BACKGROUND: behind a catch-up backlog the nightly pass would
# start once the backlog drains, possibly in office hours - and VACUUM locks
# every writer out while it runs.
@shared_task(
    name="core.db_maintenance", priority=LOW_PRIORITY, bind=True, max_retries=0
)
def db_maintenance(self, skip_vacuum=False, skip_integrity_check=False):
    """Run SQLite maintenance: optimize, WAL checkpoint, VACUUM, integrity check.

    The daily beat passes skip_vacuum/skip_integrity_check so only the cheap
    operations run; a weekly beat calls with both false for the full pass.
    """
    if not _is_sqlite():
        logger.info("Database backend is not SQLite - skipping maintenance.")
        return {"skipped": True, "reason": "not SQLite"}

    logger.info("Starting SQLite maintenance task")
    t0 = time.monotonic()
    result = _run_maintenance(
        skip_vacuum=skip_vacuum, skip_integrity_check=skip_integrity_check
    )
    result["total_ms"] = round((time.monotonic() - t0) * 1000, 1)
    logger.info("SQLite maintenance finished in %s ms", result["total_ms"])
    return result


# NORMAL like a notification: the call site that has someone watching (a
# password reset, the admin test mail) passes INTERACTIVE to send_email.
@shared_task(
    name="core.deliver_email",
    priority=NORMAL_PRIORITY,
    bind=True,
    max_retries=None,
)
def deliver_email(self, delivery_id, *, subject, text, html, headers):
    from workspace.core.services.email import (
        MAX_DELIVERY_ATTEMPTS,
        deliver,
        retry_delay,
    )

    retries = self.request.retries
    should_retry = deliver(
        delivery_id,
        subject=subject,
        text=text,
        html=html,
        headers=headers,
        # Run eagerly (development without a broker) there is no worker to
        # come back later: a retry would run inline, inside the request that
        # queued the mail, and raise out of it.
        final_attempt=self.request.is_eager or retries + 1 >= MAX_DELIVERY_ATTEMPTS,
    )
    if should_retry:
        raise self.retry(countdown=retry_delay(retries))


@shared_task(name="core.purge_email_deliveries", priority=BACKGROUND_PRIORITY)
def purge_email_deliveries():
    from workspace.core.services.email import purge_deliveries

    cutoff = timezone.now() - timedelta(days=settings.EMAIL_DELIVERY_RETENTION_DAYS)
    deleted = purge_deliveries(cutoff)
    logger.info("Purged %s email delivery records", deleted)
    return deleted
