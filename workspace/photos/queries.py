"""Access helpers for the photo library.

The library is a way of reading files the user can already open, so access is
the files module's: every queryset here starts from a ``FileService`` helper
or from ``FileShare.objects.reaching``.

A *scope* picks which of those files the library reads: ``MINE`` (the
default, the user's personal files), ``SHARED`` (files other people shared
with the user, their groups or their projects), ``ALL`` (every file the user
can open), or a ``Group`` instance for that group's folder alone.

Albums are a second way in, never a wider one: an album lists its items that
the viewer can already open (``ALL``), so an item whose file went to the
trash, was quarantined or stopped being shared with the viewer drops out of
the album instead of leaking through it.
"""

from django.contrib.auth.models import Group
from django.db.models import Count, F, Max, Min, Q, Window
from django.db.models.functions import Lower, RowNumber

from workspace.files.models import File, FileShare, Tag
from workspace.files.services import FileService
from workspace.files.services.scanning.policy import exclude_blocked
from workspace.photos.models import Album, AlbumItem, MediaItem
from workspace.photos.services.analysis import library_candidates

MINE = "mine"
SHARED = "shared"
ALL = "all"

# The one album role until albums can be shared: whoever may open an album
# may do everything to it. Sharing adds roles below it.
OWNER = "owner"


def _media(files):
    """The photos and videos of *files*, quarantined ones excluded."""
    return exclude_blocked(library_candidates(files))


def _scoped_media(user, scope):
    if scope == MINE:
        files = FileService.user_files_qs(user)
    elif scope == SHARED:
        # Sharing with someone is file-only, so the share rows are the whole
        # answer: no folder to descend into.
        files = File.objects.filter(
            pk__in=FileShare.objects.reaching(user).values("file_id"),
            deleted_at__isnull=True,
        ).exclude(owner=user)
    elif scope == ALL:
        files = File.objects.filter(
            pk__in=FileService.accessible_file_ids(user, include_deleted=False)
        )
    else:
        # Narrowed from the user's own groups, so a group they left, or never
        # joined, reads as empty rather than as someone else's folder.
        files = FileService.user_group_files_qs(user).filter(group=scope)
    return _media(files)


def library_files(user, scope=MINE):
    """The analyzed photos and videos in *scope*, as live ``File`` rows.

    Trashed files drop out through the files helpers and come back on
    restore: their MediaItem row is left alone the whole time.
    """
    return _scoped_media(user, scope).filter(media_item__isnull=False)


def unanalyzed_count(user, scope=MINE):
    """How many photos and videos in *scope* are still waiting for a MediaItem row.

    Quarantined files never get one, so counting them would announce an
    analysis that is not coming.
    """
    return (
        _scoped_media(user, scope)
        .filter(media_item__isnull=True)
        .exclude(content="")
        .exclude(content__isnull=True)
        .count()
    )


def library_tags(user, scope=MINE):
    """The user's tags carried by at least one photo in *scope*, with ``photo_count``."""
    return (
        Tag.objects.filter(
            owner=user,
            file_tags__file__in=library_files(user, scope).values("pk"),
        )
        .annotate(photo_count=Count("file_tags"))
        .order_by(Lower("name"))
    )


def library_groups(user):
    """The user's groups whose folder holds at least one photo, by name."""
    group_photos = _media(FileService.user_group_files_qs(user)).filter(
        media_item__isnull=False
    )
    return Group.objects.filter(pk__in=group_photos.values("group_id")).order_by(
        Lower("name")
    )


def has_shared_photos(user):
    """True when someone shared at least one photo with the user."""
    return library_files(user, SHARED).exists()


def media_type_counts(files):
    """How many photos and how many videos *files* holds, as a dict."""
    return files.aggregate(
        photos=Count("pk", filter=Q(media_item__media_type=MediaItem.MediaType.PHOTO)),
        videos=Count("pk", filter=Q(media_item__media_type=MediaItem.MediaType.VIDEO)),
    )


def user_albums(user):
    """The albums *user* can open: their personal ones and their groups'."""
    return Album.objects.filter(
        Q(owner=user, group__isnull=True) | Q(group__in=user.groups.values("pk"))
    )


def get_album_role(user, album):
    """*user*'s role in *album* (``OWNER``), or None when they cannot open it."""
    if album.group_id is None:
        return OWNER if album.owner_id == user.pk else None
    if user.groups.filter(pk=album.group_id).exists():
        return OWNER
    return None


def album_roles(user, albums):
    """*user*'s role in each of *albums*, keyed by album uuid, in one query.

    Albums out of reach are absent rather than mapped to None.
    """
    group_ids = set(user.groups.values_list("pk", flat=True))
    roles = {}
    for album in albums:
        if album.group_id is None:
            if album.owner_id == user.pk:
                roles[album.uuid] = OWNER
        elif album.group_id in group_ids:
            roles[album.uuid] = OWNER
    return roles


def reachable_album(user, album_uuid):
    """The album *user* can open under that uuid, or None.

    A caller getting None answers 404 without saying whether the album does
    not exist or is someone else's.
    """
    return user_albums(user).filter(uuid=album_uuid).first()


def album_files(user, album):
    """The album's photos and videos *user* can see, as live ``File`` rows.

    Empty for an album the user cannot open.
    """
    if get_album_role(user, album) is None:
        return File.objects.none()
    return library_files(user, ALL).filter(
        pk__in=AlbumItem.objects.filter(album=album).values("file_id")
    )


def visible_album_items(user, albums):
    """The items of *albums* whose file *user* can see in the library."""
    return AlbumItem.objects.filter(
        album__in=albums, file_id__in=library_files(user, ALL).values("pk")
    )


def album_summaries(user, albums):
    """What a listing shows of each album, keyed by album uuid.

    Each value is ``{"count": int, "cover_id": uuid | None}``. The cover is
    the one the album names while it is an item the viewer can see, and the
    most recently added visible item otherwise; None for an album showing
    nothing. Three queries whatever the number of albums.
    """
    albums = list(albums)
    summaries = {album.uuid: {"count": 0, "cover_id": None} for album in albums}
    if not albums:
        return summaries
    items = visible_album_items(user, [album.uuid for album in albums])
    for row in items.order_by().values("album_id").annotate(count=Count("pk")):
        summaries[row["album_id"]]["count"] = row["count"]

    chosen = {album.uuid: album.cover_id for album in albums if album.cover_id}
    if chosen:
        visible_covers = set(
            items.filter(album__cover_id=F("file_id")).values_list(
                "album_id", flat=True
            )
        )
        for album_id in visible_covers:
            summaries[album_id]["cover_id"] = chosen[album_id]

    missing = [uuid for uuid, s in summaries.items() if s["cover_id"] is None]
    if missing:
        latest = (
            items.filter(album_id__in=missing)
            .annotate(
                rank=Window(
                    RowNumber(),
                    partition_by=F("album_id"),
                    order_by=[F("added_at").desc(), F("uuid").desc()],
                )
            )
            .filter(rank=1)
            .values_list("album_id", "file_id")
        )
        for album_id, file_id in latest:
            summaries[album_id]["cover_id"] = file_id
    return summaries


def album_date_range(files):
    """The earliest and latest capture dates of *files*, as ``(first, last)``."""
    bounds = files.aggregate(
        first=Min("media_item__taken_at"), last=Max("media_item__taken_at")
    )
    return bounds["first"], bounds["last"]
