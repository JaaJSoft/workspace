"""Write side of albums: create, fill, empty and order them.

Access is the caller's to check (the album action registry, through the
views); these functions only keep the rows consistent. What an album shows
is read through ``queries.album_files``.

Manual order lives in ``AlbumItem.position``, a sparse integer: items are
``POSITION_GAP`` apart when appended, and a move writes the moved rows alone,
halfway between their new neighbours. Only when two neighbours have no room
left between them is the album renumbered, once, in its current order.
"""

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from ..models import Album, AlbumItem

POSITION_GAP = 1 << 16


def _locked(album):
    """Take the album's row lock for the rest of the transaction.

    Adds and moves on one album are serialised by it, so two of them never
    pick the same free positions. SQLite ignores it and relies on its single
    writer instead.
    """
    Album.objects.select_for_update().filter(pk=album.pk).first()


def _touch(album):
    Album.objects.filter(pk=album.pk).update(updated_at=timezone.now())


def create_album(owner, title, *, description="", sort_mode=None, files=()):
    """A new personal album of *owner*, holding *files* in that order."""
    with transaction.atomic():
        album = Album.objects.create(
            owner=owner,
            title=title,
            description=description,
            sort_mode=sort_mode or Album.SortMode.CAPTURE_DATE,
        )
        if files:
            add_items(album, files, added_by=owner)
    return album


def add_items(album, files, *, added_by):
    """Append *files* to the end of *album*, skipping those already in it.

    Returns how many were added.
    """
    with transaction.atomic():
        _locked(album)
        present = set(
            AlbumItem.objects.filter(album=album, file__in=files).values_list(
                "file_id", flat=True
            )
        )
        new = [f for f in dict.fromkeys(files) if f.pk not in present]
        if not new:
            return 0
        last = AlbumItem.objects.filter(album=album).aggregate(p=Max("position"))["p"]
        start = (last or 0) + POSITION_GAP
        now = timezone.now()
        AlbumItem.objects.bulk_create(
            [
                AlbumItem(
                    album=album,
                    file=f,
                    added_by=added_by,
                    added_at=now,
                    position=start + i * POSITION_GAP,
                )
                for i, f in enumerate(new)
            ]
        )
        _touch(album)
    return len(new)


def remove_items(album, file_ids):
    """Take the files *file_ids* out of *album*; returns how many were in it.

    A cover among them is forgotten with it, so the album goes back to the
    fallback rather than naming a file it no longer holds.
    """
    file_ids = set(file_ids)
    with transaction.atomic():
        removed, _ = AlbumItem.objects.filter(
            album=album, file_id__in=file_ids
        ).delete()
        if album.cover_id in file_ids:
            album.cover = None
            album.save(update_fields=["cover", "updated_at"])
        elif removed:
            _touch(album)
    return removed


def _ordered(album):
    return AlbumItem.objects.filter(album=album).order_by("position", "file_id")


def _renumber(album):
    """Space every item of *album* ``POSITION_GAP`` apart, in current order."""
    items = list(_ordered(album))
    for i, item in enumerate(items, start=1):
        item.position = i * POSITION_GAP
    AlbumItem.objects.bulk_update(items, ["position"])


def _neighbours(album, moving, before, after):
    """The positions the moved block goes between, as ``(low, high)``.

    Either bound is None at an end of the album. The moved items are left
    out, so moving a block next to itself is not a special case.
    """
    rest = _ordered(album).exclude(file_id__in=moving)
    if after is not None:
        anchor = rest.filter(file_id=after).first()
        following = rest.filter(position__gte=anchor.position).exclude(
            position=anchor.position, file_id__lte=after
        )
        high = following.values_list("position", flat=True).first()
        return anchor.position, high
    if before is not None:
        anchor = rest.filter(file_id=before).first()
        preceding = (
            rest.filter(position__lte=anchor.position)
            .exclude(position=anchor.position, file_id__gte=before)
            .order_by("-position", "-file_id")
        )
        low = preceding.values_list("position", flat=True).first()
        return low, anchor.position
    last = rest.order_by("-position", "-file_id").values_list("position", flat=True)
    return last.first(), None


def move_items(album, file_ids, *, before=None, after=None):
    """Move the items *file_ids* of *album*, in that order, to one place.

    Right after the item *after*, right before the item *before*, or at the
    end when neither is given. The anchors are file ids of items of the
    album that are not being moved; ValueError otherwise.
    """
    file_ids = list(dict.fromkeys(file_ids))
    anchor = after if after is not None else before
    if anchor is not None and anchor in file_ids:
        raise ValueError("an item cannot be moved next to itself")
    with transaction.atomic():
        _locked(album)
        items = {
            item.file_id: item
            for item in AlbumItem.objects.filter(album=album, file_id__in=file_ids)
        }
        if len(items) != len(file_ids):
            raise ValueError("not an item of the album")
        if (
            anchor is not None
            and not AlbumItem.objects.filter(album=album, file_id=anchor).exists()
        ):
            raise ValueError("anchor is not an item of the album")

        count = len(file_ids)
        low, high = _neighbours(album, file_ids, before, after)
        if low is not None and high is not None and high - low <= count:
            _renumber(album)
            low, high = _neighbours(album, file_ids, before, after)

        if low is None and high is None:
            positions = [(i + 1) * POSITION_GAP for i in range(count)]
        elif high is None:
            positions = [low + (i + 1) * POSITION_GAP for i in range(count)]
        elif low is None:
            positions = [high - (count - i) * POSITION_GAP for i in range(count)]
        else:
            step = (high - low) // (count + 1)
            positions = [low + (i + 1) * step for i in range(count)]

        moved = [items[file_id] for file_id in file_ids]
        for item, position in zip(moved, positions, strict=True):
            item.position = position
        AlbumItem.objects.bulk_update(moved, ["position"])
        _touch(album)
