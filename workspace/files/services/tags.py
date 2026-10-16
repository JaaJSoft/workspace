"""Tag collection operations: usage counts, lookup by name, merge, purge."""

from django.db import transaction
from django.db.models import Count, Prefetch
from django.db.models.functions import Lower

from ..models import FileTag, Tag

_MAX_TAG_NAME = Tag._meta.get_field("name").max_length


class TagMergeError(ValueError):
    """A merge the caller asked for cannot be done; `code` names why."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def viewer_file_tags(user):
    """Prefetch of ``file_tags`` holding only *user*'s own tags, by name.

    Tags are private to their owner, but a file reaches other people through
    shares and groups: an unscoped prefetch would show them the owner's tags.
    """
    return Prefetch(
        "file_tags",
        queryset=FileTag.objects.filter(tag__owner=user)
        .select_related("tag")
        .order_by(Lower("tag__name")),
    )


def tags_with_usage(user):
    """The user's tags, each annotated with `file_count`.

    Usage counts every FileTag row, trashed files included: the trash is
    reversible, so the assignment still exists, and "unused" in
    `purge_unused_tags` has to mean the same thing as a count of zero here.
    """
    return Tag.objects.filter(owner=user).annotate(file_count=Count("file_tags"))


def get_or_create_tag(owner, name, icon="") -> Tag | None:
    """The owner's tag by that name, matched case-insensitively.

    *icon* is only given to a tag created here: an existing one keeps its own.

    A tag list is a flat namespace of names, so a second 'Invoices' next to
    an existing 'invoices' would read as a duplicate in the tag picker.
    ``None`` when *name* holds nothing usable.
    """
    name = name.strip()[:_MAX_TAG_NAME].strip()
    if not name:
        return None
    existing = Tag.objects.filter(owner=owner, name__iexact=name).first()
    if existing is not None:
        return existing
    tag, _ = Tag.objects.get_or_create(owner=owner, name=name, defaults={"icon": icon})
    return tag


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
