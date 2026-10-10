"""Persistence of processor attempts that failed (ProcessingFailure rows).

A file a processor can never handle - truncated bytes, an unsupported variant
of a supported format, a blob missing from storage - would otherwise be read
again by every hourly catch-up pass, forever. Recording attempts lets the
catch-up park such a file once it has burned its budget with that processor;
the other processors keep running on it.

The counters are scoped to the file's current content: a content write drops
every row of the file (clear_failures), so repaired bytes get a fresh budget.
"""

from collections import defaultdict
from datetime import timedelta

from django.db.models import F
from django.utils import timezone

from ..models import ProcessingFailure

MAX_ATTEMPTS = 3

# Parking expires: not every cause is permanent. A blob briefly missing during
# a storage outage, or a decoder broken by a bad deploy, can outlast three
# hourly passes, and treating those as final strands the file for good. A
# genuinely broken file therefore costs one attempt a day, which is the price
# of not stranding a file whose failure was transient.
PARKED_RETRY_AFTER = timedelta(days=1)

_MAX_ERROR_LENGTH = ProcessingFailure._meta.get_field("last_error").max_length


def record_failure(file_obj, processor, error):
    """Create or increment *file_obj*'s failure row for *processor*."""
    message = str(error)[:_MAX_ERROR_LENGTH]
    now = timezone.now()

    row, created = ProcessingFailure.objects.get_or_create(
        file=file_obj,
        processor=processor,
        defaults={"attempts": 1, "last_attempt_at": now, "last_error": message},
    )
    if not created:
        ProcessingFailure.objects.filter(pk=row.pk).update(
            attempts=F("attempts") + 1,
            last_attempt_at=now,
            last_error=message,
        )


def clear_failure(file_obj, processor):
    """Drop *file_obj*'s failure row for *processor*, if any."""
    ProcessingFailure.objects.filter(file=file_obj, processor=processor).delete()


def clear_failures(file_obj):
    """Drop every failure row of *file_obj*: its content was replaced."""
    ProcessingFailure.objects.filter(file=file_obj).delete()


def parked_failures(processor=None):
    """The rows parked recently enough to skip, for one processor or all."""
    rows = ProcessingFailure.objects.filter(
        attempts__gte=MAX_ATTEMPTS,
        last_attempt_at__gte=timezone.now() - PARKED_RETRY_AFTER,
    )
    if processor is not None:
        rows = rows.filter(processor=processor)
    return rows


def parked_file_ids(processor):
    """File ids *processor* skips for now, as an ``.exclude()`` subquery.

    A row past PARKED_RETRY_AFTER drops out, so the file is attempted once more
    and, if it fails again, parked for another window. Its ``attempts`` keeps
    climbing past the budget on purpose: this filter matches with ``__gte``, so
    an overshooting row stays parked.
    """
    return parked_failures(processor).values("file_id")


def failure_count(processor=None):
    """Number of failing files, for one processor or all of them."""
    rows = ProcessingFailure.objects.all()
    if processor is not None:
        rows = rows.filter(processor=processor)
    return rows.count()


def retry_failures(failures):
    """Unpark the files behind *failures* (a queryset) and queue each of them
    with the processor it failed in.

    Returns the number of rows unparked.
    """
    from .processors import get_processor, queue_files

    by_processor = defaultdict(list)
    for processor, file_id in failures.values_list("processor", "file_id"):
        by_processor[processor].append(file_id)
    unparked = 0
    for name, file_ids in by_processor.items():
        unparked += ProcessingFailure.objects.filter(
            processor=name, file_id__in=file_ids
        ).delete()[0]
        processor = get_processor(name)
        if processor is not None:
            queue_files(processor, file_ids)
    return unparked
