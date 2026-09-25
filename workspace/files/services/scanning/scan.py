"""Scan a file's content for malware and record the verdict (FileScan rows).

Runs off-request: from the files.scan_file task, which the file-event handler
queues once an upload or a content replacement has committed, and from the
hourly catch-up (services/catch_up.py) for whatever that path missed.
"""

import logging
import time

from django.conf import settings
from django.db.models import F, Q
from django.utils import timezone

from workspace.common.logging import scrub

from ...models import File, FileScan

logger = logging.getLogger(__name__)


def scanning_enabled():
    return bool(getattr(settings, "FILES_MALWARE_SCAN_ENABLED", False))


def pending_scan_qs(*, reanalyze=False):
    """Live files whose verdict is missing or stale.

    With *reanalyze*, every live file, verdict or not.
    """
    qs = File.objects.alive().with_blob()
    if reanalyze:
        return qs
    # "Needs scanning" is not the same as "never scanned". A file whose
    # content changed after its verdict was written still carries that
    # verdict, so filtering on scan__isnull alone would strand it: the
    # CONTENT_REPLACED event normally queues a fresh scan, but if that event
    # was lost - a worker killed, a broker flushed - nothing else would ever
    # revisit the file.
    #
    # An empty hash on either side means the bytes cannot be vouched for: a
    # verdict written before this field existed, or a file whose own hash
    # could not be computed. Both count as needing a scan rather than as a
    # match, which two empty strings would otherwise compare as.
    return qs.filter(
        Q(scan__isnull=True)
        | Q(scan__content_hash="")
        | Q(content_hash="")
        | ~Q(scan__content_hash=F("content_hash"))
    )


def scan_for_catch_up(file_obj):
    """Scan *file_obj* with the configured scanner; True when a verdict was written."""
    from .registry import get_scanner

    scanner = get_scanner()
    if scanner is None:
        return False
    return scan_and_record(file_obj, scanner)["status"] in FileScan.Status.values


def scan_and_record(file_obj, scanner):
    """Scan *file_obj* with *scanner* and record the verdict.

    Returns a dict whose "status" is the verdict, or why nothing was written.
    """
    from ...metrics import FILES_MALWARE_SCAN_DURATION, FILES_MALWARE_SCAN_RESULT
    from ..search_index import unindex_file
    from .base import ScanVerdict
    from .capped import CappedReader
    from .policy import blocked_statuses

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
    # and the catch-up without --reanalyze both leave an existing row alone.
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
    existing = (
        FileScan.objects.filter(file=file_obj).values("status", "content_hash").first()
        or {}
    )
    was_blocked = existing.get("status") in blocked

    defaults = {
        "status": verdict.status,
        "signature": verdict.signature,
        "detail": verdict.detail,
        # The hash captured before the blob was opened, not a fresh read:
        # it is the one the verdict actually describes, and the staleness
        # check above has just confirmed the row still holds it.
        "content_hash": scanned_hash,
        "scanned_at": timezone.now(),
    }
    # An administrator's clearance is pinned to the hash on this row, and the
    # line above moves that pin. Leaving the columns alone would hand a verdict
    # about bytes nobody vouched for the clearance granted to the previous
    # ones. A rescan of the same bytes keeps it, which is the whole point of
    # the action - the catch-up would otherwise undo every clearance it passes.
    if existing.get("content_hash") != scanned_hash:
        defaults["overridden_at"] = None
        defaults["overridden_by"] = None
        defaults["override_reason"] = ""

    FileScan.objects.update_or_create(file=file_obj, defaults=defaults)
    FILES_MALWARE_SCAN_RESULT.labels(result=verdict.status).inc()

    if verdict.status in blocked:
        unindex_file(file_obj)
        if file_obj.has_thumbnail:
            from ..thumbnails.generation import delete_thumbnail

            delete_thumbnail(file_obj.uuid)
            file_obj.has_thumbnail = False
            file_obj.save(update_fields=["has_thumbnail"])
    elif was_blocked:
        from .override import restore_after_unblock

        restore_after_unblock(file_obj)

    return {"status": verdict.status, "signature": verdict.signature}
