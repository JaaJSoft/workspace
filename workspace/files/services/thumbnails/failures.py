"""Persistence of thumbnail generation attempts that failed.

A file that can never produce a thumbnail - truncated bytes, an unsupported
variant of a supported label, a blob missing from storage - would otherwise be
re-decoded by every hourly catch-up pass, forever. Recording attempts lets the
catch-up park such a file once it has burned its budget.

The counter is scoped to the file's current content: callers drop the row when
the content is replaced, so repaired bytes get a fresh budget.
"""

from datetime import timedelta

from django.db.models import F
from django.utils import timezone

from ...models import ThumbnailFailure

MAX_THUMBNAIL_ATTEMPTS = 3

# Parking expires: not every cause is permanent. A blob briefly missing during
# a storage outage, or a rasterizer broken by a bad deploy, can outlast three
# hourly passes, and treating those as final strands the file for good. A
# genuinely broken file therefore costs one decode a day, which is the price of
# not stranding a file whose failure was transient.
PARKED_RETRY_AFTER = timedelta(days=1)

_MAX_ERROR_LENGTH = ThumbnailFailure._meta.get_field("last_error").max_length


def record_failure(file_obj, error):
    """Create or increment the failure row for *file_obj*."""
    message = str(error)[:_MAX_ERROR_LENGTH]
    now = timezone.now()

    row, created = ThumbnailFailure.objects.get_or_create(
        file=file_obj,
        defaults={"attempts": 1, "last_attempt_at": now, "last_error": message},
    )
    if not created:
        ThumbnailFailure.objects.filter(pk=row.pk).update(
            attempts=F("attempts") + 1,
            last_attempt_at=now,
            last_error=message,
        )


def clear_failure(file_obj):
    """Drop the failure row for *file_obj*, if any."""
    ThumbnailFailure.objects.filter(file=file_obj).delete()


def parked_file_ids():
    """File ids parked recently enough to skip, as an ``.exclude()`` subquery.

    A row past PARKED_RETRY_AFTER drops out, so the file is attempted once more
    and, if it fails again, parked for another window. Its ``attempts`` keeps
    climbing past the budget on purpose: this filter matches with ``__gte``, so
    an overshooting row stays parked.
    """
    return ThumbnailFailure.objects.filter(
        attempts__gte=MAX_THUMBNAIL_ATTEMPTS,
        last_attempt_at__gte=timezone.now() - PARKED_RETRY_AFTER,
    ).values("file_id")


def clear_all_failures():
    """Purge every failure row so parked files are retried.

    Returns the number of rows deleted.
    """
    deleted, _ = ThumbnailFailure.objects.all().delete()
    return deleted


def parked_count():
    """Number of files currently parked (one failure row per file)."""
    return ThumbnailFailure.objects.count()


def retry_failures(failures):
    """Unpark the files behind *failures* (a queryset) and queue each of them.

    Returns the number of files unparked.
    """
    from ..catch_up import get_catch_up, queue_files

    file_ids = list(failures.values_list("file_id", flat=True))
    ThumbnailFailure.objects.filter(file_id__in=file_ids).delete()
    return queue_files(get_catch_up("thumbnails"), file_ids)
