"""Favorites and tags applied to files an import already brought over.

Both writers are idempotent: a re-run of the same import re-reads the remote
and must not end up with a second favorite or a second tag on the same file.
"""

from itertools import batched

from workspace.files.models import FileFavorite, FileTag
from workspace.files.services.tags import get_or_create_tag

_CHUNK = 500


def mark_favorites(owner, file_uuids) -> int:
    """Favorite every file for *owner*; returns how many were not already."""
    added = 0
    for chunk in batched(dict.fromkeys(file_uuids), _CHUNK, strict=False):
        missing = set(chunk) - _existing(
            FileFavorite.objects.filter(owner=owner), chunk
        )
        FileFavorite.objects.bulk_create(
            [FileFavorite(owner=owner, file_id=uuid) for uuid in missing],
            ignore_conflicts=True,
        )
        added += len(missing)
    return added


def apply_tag(owner, name, file_uuids) -> int:
    """Attach *owner*'s tag named *name*, creating it on first use, to every
    file; returns how many did not carry it yet."""
    uuids = list(dict.fromkeys(file_uuids))
    tag = get_or_create_tag(owner, name) if uuids else None
    if tag is None:
        return 0
    added = 0
    for chunk in batched(uuids, _CHUNK, strict=False):
        missing = set(chunk) - _existing(FileTag.objects.filter(tag=tag), chunk)
        FileTag.objects.bulk_create(
            [FileTag(tag=tag, file_id=uuid) for uuid in missing],
            ignore_conflicts=True,
        )
        added += len(missing)
    return added


def _existing(queryset, file_uuids) -> set:
    return set(
        queryset.filter(file_id__in=file_uuids).values_list("file_id", flat=True)
    )
