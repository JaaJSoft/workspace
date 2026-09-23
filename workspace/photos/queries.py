"""Access helpers for the photo library.

The library is a way of reading files the user can already browse, so access
is the files module's: every queryset here starts from
``FileService.user_files_qs`` or ``FileService.user_group_files_qs``.

A *scope* picks which of those the library reads: ``MINE`` (the default, the
user's personal files), ``ALL`` (those plus every group folder the user is a
member of), or a ``Group`` instance for that group's folder alone.
"""

from django.contrib.auth.models import Group
from django.db.models import Count
from django.db.models.functions import Lower

from workspace.files.models import File, Tag
from workspace.files.services import FileService
from workspace.files.services.scanning.policy import exclude_blocked
from workspace.photos.services.analysis import PHOTO_LABELS

MINE = "mine"
ALL = "all"


def _scoped_images(user, scope):
    """Live raster images in *scope*, quarantined ones excluded."""
    if scope == MINE:
        files = FileService.user_files_qs(user)
    elif scope == ALL:
        files = FileService.user_files_qs(user) | FileService.user_group_files_qs(user)
    else:
        # Narrowed from the user's own groups, so a group they left, or never
        # joined, reads as empty rather than as someone else's folder.
        files = FileService.user_group_files_qs(user).filter(group=scope)
    return exclude_blocked(
        files.filter(node_type=File.NodeType.FILE, type__in=PHOTO_LABELS)
    )


def library_files(user, scope=MINE):
    """The analyzed photos in *scope*, as live ``File`` rows.

    Trashed files drop out through the files helpers and come back on
    restore: their Photo row is left alone the whole time.
    """
    return _scoped_images(user, scope).filter(photo__isnull=False)


def unanalyzed_count(user, scope=MINE):
    """How many raster images in *scope* are still waiting for a Photo row.

    Quarantined files never get one, so counting them would announce an
    analysis that is not coming.
    """
    return (
        _scoped_images(user, scope)
        .filter(photo__isnull=True)
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
    return Group.objects.filter(
        pk__in=library_files(user, ALL).filter(group__isnull=False).values("group_id")
    ).order_by(Lower("name"))
