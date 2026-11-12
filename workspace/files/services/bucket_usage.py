"""The per-bucket byte counter behind quota checks.

``quota.personal_usage`` / ``quota.group_usage`` sum ``size`` over every file
row of a bucket. That is the definition, and the admin and the settings page
keep reading it; but a quota check runs on every write, and a sum whose cost
grows with the bucket makes a bulk import quadratic - SQLite ignores the
covering index's ``INCLUDE`` and reads one row per file.

``StorageUsage`` holds the same total, one row per bucket. Every write that
changes a bucket's total moves the row by the difference, inside the
transaction that makes the change, so the two commit or roll back together:

- an INSERT of a file row (``names._insert``);
- a content write that changes a row's size (``content_writes``);
- a move that changes a subtree's bucket (``tree.move``);
- a hard delete, through the ``post_delete`` receiver, which sees queryset
  and cascade deletes too;
- a group deletion, whose rows fall back into their owners' buckets.

Soft delete and restore leave the counter alone: trashed rows count.

A bucket nobody has checked yet has no row. A write leaves it that way, and
the first read seeds it from the aggregate. Anything that changes file rows
past these paths makes the counter drift; ``reconcile`` resets it to the
aggregate every night and logs what it corrected.
"""

import logging
from collections import Counter
from contextlib import contextmanager

from django.db import IntegrityError, transaction
from django.db.models import F, Sum

from ..models import File, StorageUsage

logger = logging.getLogger(__name__)


def _pk(value):
    return getattr(value, "pk", value)


def bucket_key(owner_id, group_id):
    """The bucket a row with these columns counts against."""
    if group_id is not None:
        return (None, group_id)
    return (owner_id, None)


def _bucket_filter(key):
    owner_id, group_id = key
    if group_id is not None:
        return {"group_id": group_id}
    return {"owner_id": owner_id}


def _aggregate(key):
    owner_id, group_id = key
    files = File.objects.filter(node_type=File.NodeType.FILE)
    if group_id is not None:
        files = files.filter(group_id=group_id)
    else:
        files = files.filter(owner_id=owner_id, group__isnull=True)
    return files.aggregate(total=Sum("size"))["total"] or 0


def charge(totals):
    """Move each bucket of *totals* (``{bucket_key: delta}``) by its delta.

    A bucket without a row is left without one: its first read seeds it
    from the aggregate, which then includes this write.
    """
    for key, delta in totals.items():
        if delta:
            StorageUsage.objects.filter(**_bucket_filter(key)).update(
                bytes=F("bytes") + delta
            )


def charge_row(owner_id, group_id, delta):
    """``charge`` for a single bucket."""
    charge({bucket_key(owner_id, group_id): delta})


def bucket_totals(rows):
    """Bytes the file rows of *rows* hold, per bucket."""
    totals = Counter()
    sums = (
        rows.filter(node_type=File.NodeType.FILE)
        .order_by()
        .values("owner_id", "group_id")
        .annotate(total=Sum("size"))
    )
    for row in sums:
        totals[bucket_key(row["owner_id"], row["group_id"])] += row["total"] or 0
    return totals


@contextmanager
def tracking(rows):
    """Charge the buckets for whatever the block does to *rows*.

    *rows* is a callable returning the queryset, called before and after the
    block: a subtree filter built from a path has to be rebuilt once the
    path moved. The difference is charged in the block's transaction.
    """
    with transaction.atomic():
        before = bucket_totals(rows())
        yield
        after = bucket_totals(rows())
        after.subtract(before)
        charge(after)


def used_bytes(*, owner, group, plus=None):
    """The bucket's total from its counter, seeding the counter when missing.

    *plus* is an expression of the bucket added to the total in the same
    query: the open upload sessions' reservations, for a quota check.
    """
    key = bucket_key(_pk(owner), _pk(group))
    counter = StorageUsage.objects.filter(**_bucket_filter(key))
    if plus is not None:
        counter = counter.annotate(total=F("bytes") + plus)
    else:
        counter = counter.annotate(total=F("bytes"))
    row = counter.values_list("total", flat=True).first()
    if row is not None:
        return row
    # The aggregate and the INSERT share a transaction, so on SQLite no
    # write can land between them; elsewhere one that does is the drift the
    # nightly reconcile is for.
    try:
        with transaction.atomic():
            StorageUsage.objects.create(bytes=_aggregate(key), **_bucket_filter(key))
    except IntegrityError:
        pass  # Seeded by a concurrent reader meanwhile.
    return counter.values_list("total", flat=True).get()


def reconcile():
    """Reset every counter that drifted from its aggregate; return how many.

    Each row is compared and corrected in its own short transaction, and
    only overwritten when it still holds the value that was compared, so a
    write landing meanwhile is not erased.
    """
    corrected = 0
    pks = list(StorageUsage.objects.values_list("pk", flat=True))
    for pk in pks:
        with transaction.atomic():
            usage = StorageUsage.objects.filter(pk=pk).first()
            if usage is None:
                continue
            actual = _aggregate((usage.owner_id, usage.group_id))
            if actual == usage.bytes:
                continue
            if StorageUsage.objects.filter(pk=pk, bytes=usage.bytes).update(
                bytes=actual
            ):
                corrected += 1
                logger.warning(
                    "Storage usage of %s %s drifted: counter %d, actual %d",
                    "group" if usage.group_id else "user",
                    usage.group_id or usage.owner_id,
                    usage.bytes,
                    actual,
                )
    return corrected


def release_group(group):
    """Move *group*'s bytes to its rows' owners, ahead of its deletion."""
    per_owner = (
        File.objects.filter(group_id=_pk(group), node_type=File.NodeType.FILE)
        .order_by()
        .values("owner_id")
        .annotate(total=Sum("size"))
    )
    charge({(row["owner_id"], None): row["total"] or 0 for row in per_owner})
