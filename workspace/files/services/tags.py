"""Tag collection operations: usage counts, merge, purge."""

from django.db import transaction
from django.db.models import Count

from ..models import FileTag, Tag


class TagMergeError(ValueError):
    """A merge the caller asked for cannot be done; `code` names why."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def tags_with_usage(user):
    """The user's tags, each annotated with `file_count`.

    Usage counts every FileTag row, trashed files included: the trash is
    reversible, so the assignment still exists, and "unused" in
    `purge_unused_tags` has to mean the same thing as a count of zero here.
    """
    return Tag.objects.filter(owner=user).annotate(file_count=Count("file_tags"))


def merge_tags(source, target):
    """Move every assignment of `source` onto `target`, then delete `source`.

    A file carrying both tags would violate `unique_file_tag` on the move,
    so its source row is dropped instead: it stays tagged once, by `target`.
    """
    if source.pk == target.pk:
        raise TagMergeError("same_tag")
    if source.owner_id != target.owner_id:
        raise TagMergeError("cross_owner")

    with transaction.atomic():
        already_tagged = FileTag.objects.filter(tag=target).values("file_id")
        source_rows = FileTag.objects.filter(tag=source)
        source_rows.filter(file_id__in=already_tagged).delete()
        source_rows.update(tag=target)
        source.delete()
    return target


def purge_unused_tags(user):
    """Delete the user's tags with no assignment; returns how many went."""
    deleted, _ = Tag.objects.filter(owner=user, file_tags__isnull=True).delete()
    return deleted
