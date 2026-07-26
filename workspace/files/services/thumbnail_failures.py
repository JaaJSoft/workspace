"""Persistence of thumbnail generation attempts that failed.

A file that can never produce a thumbnail - truncated bytes, an unsupported
variant of a supported label, a blob missing from storage - would otherwise be
re-decoded by every hourly backfill pass, forever. Recording attempts lets the
backfill park such a file once it has burned its budget.

The counter is scoped to the file's current content: callers drop the row when
the content is replaced, so repaired bytes get a fresh budget.
"""

from django.db.models import F
from django.utils import timezone

from ..models import ThumbnailFailure

MAX_THUMBNAIL_ATTEMPTS = 3

_MAX_ERROR_LENGTH = 200


def record_failure(file_obj, error):
    """Create or increment the failure row for *file_obj*.

    Returns the resulting attempt count.
    """
    message = str(error)[:_MAX_ERROR_LENGTH]
    now = timezone.now()

    row, created = ThumbnailFailure.objects.get_or_create(
        file=file_obj,
        defaults={"attempts": 1, "last_attempt_at": now, "last_error": message},
    )
    if created:
        return 1

    ThumbnailFailure.objects.filter(pk=row.pk).update(
        attempts=F("attempts") + 1,
        last_attempt_at=now,
        last_error=message,
    )
    # The database performs the authoritative atomic increment. The returned
    # value is derived rather than re-read: it is informational only, so a
    # concurrent bump making it stale by one is not worth an extra query.
    return row.attempts + 1


def clear_failure(file_obj):
    """Drop the failure row for *file_obj*, if any."""
    ThumbnailFailure.objects.filter(file=file_obj).delete()


def parked_file_ids():
    """File ids that burned their attempt budget, as an ``.exclude()`` subquery."""
    return ThumbnailFailure.objects.filter(attempts__gte=MAX_THUMBNAIL_ATTEMPTS).values(
        "file_id"
    )


def count_parked_since(moment):
    """How many files reached the attempt budget at or after *moment*."""
    return ThumbnailFailure.objects.filter(
        last_attempt_at__gte=moment,
        attempts__gte=MAX_THUMBNAIL_ATTEMPTS,
    ).count()


def clear_all_failures():
    """Purge every failure row so parked files are retried.

    Returns the number of rows deleted.
    """
    deleted, _ = ThumbnailFailure.objects.all().delete()
    return deleted
