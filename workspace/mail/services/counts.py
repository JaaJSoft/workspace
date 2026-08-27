"""Folder and label counters, recomputed from the messages they count.

Every write that can change what a counter counts - a flag change, a move, a
soft delete, a label attached - is followed by one of these. They live in a
service rather than next to a view because the mail UI, the REST endpoints,
the sync tasks and the AI tools all move messages around, and a counter
refreshed in only some of those paths is a counter nobody can trust.
"""

from django.db.models import Count, Q
from django.utils import timezone

from ..models import MailFolder, MailLabel, MailMessage, MailMessageLabel


def _apply_grouped_counts(targets, group_key, grouped_qs, field_map):
    """Apply a grouped aggregate onto many target rows via a single ``bulk_update``.

    Shared plumbing for ``refresh_folders_counts_bulk`` and ``refresh_label_counts``:
    both reduce to "GROUP BY FK_id on a source table, then push the aggregated
    counts back onto a set of target rows". This helper handles the generic
    part: build a ``{pk: row}`` lookup, zero-default rows absent from the
    grouped result, set ``updated_at`` manually, and ``bulk_update``.

    Args:
        targets: List of already-loaded target model instances. The helper
            does NOT re-fetch them - callers pass the objects they want
            written back. Empty list is a no-op.
        group_key: Field name present in each ``grouped_qs`` row that maps to
            the target's primary key (e.g. ``'folder_id'``, ``'label_id'``).
        grouped_qs: An iterable of dicts - typically a queryset evaluated via
            ``.values(group_key).annotate(...)``. Rows are matched to targets
            by ``row[group_key] == target.pk``.
        field_map: ``{aggregate_alias: target_field_name}``. Targets absent
            from ``grouped_qs`` receive ``0`` for every mapped field.

    Notes:
        - Django's ``bulk_update`` bypasses ``auto_now``; ``updated_at`` is set
          manually to preserve the semantics of the original ``save()`` path.
        - All ``targets`` must share the same model class.
    """
    if not targets:
        return
    by_pk = {row[group_key]: row for row in grouped_qs}
    now = timezone.now()
    for target in targets:
        data = by_pk.get(target.pk, {})
        for alias, field_name in field_map.items():
            setattr(target, field_name, data.get(alias, 0))
        target.updated_at = now
    type(targets[0]).objects.bulk_update(
        targets,
        list(field_map.values()) + ["updated_at"],
    )


def refresh_folder_counts(folder):
    """Recompute message_count and unread_count for a single folder.

    Single-folder fast path: 1 aggregate + 1 UPDATE via ``save()``. For N
    folders, prefer ``refresh_folders_counts_bulk`` which collapses the work
    into 2 queries total regardless of N.
    """
    counts = MailMessage.objects.filter(
        folder=folder,
        deleted_at__isnull=True,
    ).aggregate(
        message_count=Count("pk"),
        unread_count=Count("pk", filter=Q(is_read=False)),
    )
    folder.message_count = counts["message_count"]
    folder.unread_count = counts["unread_count"]
    folder.save(update_fields=["message_count", "unread_count", "updated_at"])


def refresh_folders_counts_bulk(folder_ids):
    """Refresh message_count + unread_count for many folders in 2 queries.

    Replaces the naive ``for folder in ...: refresh_folder_counts(folder)``
    pattern (2N queries) with a single ``GROUP BY folder_id`` aggregate and a
    single ``bulk_update`` via :func:`_apply_grouped_counts`.
    """
    folder_ids = list(folder_ids)
    if not folder_ids:
        return
    grouped = (
        MailMessage.objects.filter(
            folder_id__in=folder_ids,
            deleted_at__isnull=True,
        )
        .values("folder_id")
        .annotate(
            msg_count=Count("pk"),
            unread_cnt=Count("pk", filter=Q(is_read=False)),
        )
    )
    folders = list(MailFolder.objects.filter(uuid__in=folder_ids))
    _apply_grouped_counts(
        folders,
        "folder_id",
        grouped,
        {"msg_count": "message_count", "unread_cnt": "unread_count"},
    )


def refresh_label_counts(labels):
    """Recompute unread_count for one or more labels in 2 queries.

    Accepts a single MailLabel, a queryset, or any iterable of MailLabels.
    Uses a single ``GROUP BY label_id`` aggregate + ``bulk_update`` regardless
    of N, instead of the previous 2N queries (1 COUNT + 1 UPDATE per label).
    """
    if isinstance(labels, MailLabel):
        labels = [labels]
    labels = list(labels)
    if not labels:
        return
    grouped = (
        MailMessageLabel.objects.filter(
            label_id__in=[lbl.pk for lbl in labels],
            message__is_read=False,
            message__deleted_at__isnull=True,
        )
        .values("label_id")
        .annotate(
            unread_cnt=Count("pk"),
        )
    )
    _apply_grouped_counts(
        labels,
        "label_id",
        grouped,
        {"unread_cnt": "unread_count"},
    )


def refresh_message_label_counts(message):
    """Refresh unread counts for all labels attached to a message."""
    label_ids = MailMessageLabel.objects.filter(message=message).values_list(
        "label_id", flat=True
    )
    if label_ids:
        refresh_label_counts(MailLabel.objects.filter(pk__in=label_ids))
