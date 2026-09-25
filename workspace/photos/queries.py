"""Access helpers for the photo library.

The library is a way of reading files the user can already open, so access is
the files module's: every queryset here starts from a ``FileService`` helper
or from ``FileShare.objects.reaching``.

A *scope* picks which of those files the library reads: ``MINE`` (the
default, the user's personal files), ``SHARED`` (files other people shared
with the user, their groups or their projects), ``ALL`` (every file the user
can open), or a ``Group`` instance for that group's folder alone.
"""

from django.contrib.auth.models import Group
from django.db.models import Count
from django.db.models.functions import Lower

from workspace.files.models import File, FileShare, Tag
from workspace.files.services import FileService
from workspace.files.services.scanning.policy import exclude_blocked
from workspace.photos.services.analysis import PHOTO_LABELS

MINE = "mine"
SHARED = "shared"
ALL = "all"


def _images(files):
    """The raster images of *files*, quarantined ones excluded."""
    return exclude_blocked(
        files.filter(node_type=File.NodeType.FILE, type__in=PHOTO_LABELS)
    )


def _scoped_images(user, scope):
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
    return _images(files)


def library_files(user, scope=MINE):
    """The analyzed photos in *scope*, as live ``File`` rows.

    Trashed files drop out through the files helpers and come back on
    restore: their MediaItem row is left alone the whole time.
    """
    return _scoped_images(user, scope).filter(media_item__isnull=False)


def unanalyzed_count(user, scope=MINE):
    """How many raster images in *scope* are still waiting for a MediaItem row.

    Quarantined files never get one, so counting them would announce an
    analysis that is not coming.
    """
    return (
        _scoped_images(user, scope)
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
    group_photos = _images(FileService.user_group_files_qs(user)).filter(
        media_item__isnull=False
    )
    return Group.objects.filter(pk__in=group_photos.values("group_id")).order_by(
        Lower("name")
    )


def has_shared_photos(user):
    """True when someone shared at least one photo with the user."""
    return library_files(user, SHARED).exists()
