"""Access helpers for the photo library.

The library is a way of reading the user's own files, so access is the files
module's: every queryset here starts from ``FileService.user_files_qs``.
"""

from django.db.models import Count
from django.db.models.functions import Lower

from workspace.files.models import File, Tag
from workspace.files.services import FileService
from workspace.files.services.scanning.policy import exclude_blocked
from workspace.photos.services.analysis import PHOTO_LABELS


def library_files(user):
    """The user's analyzed photos, as live ``File`` rows.

    Personal files only, like the rest of "My Files", and never a quarantined
    one. Trashed files drop out through ``user_files_qs`` and come back on
    restore: their Photo row is left alone the whole time.
    """
    return exclude_blocked(
        FileService.user_files_qs(user).filter(
            node_type=File.NodeType.FILE,
            type__in=PHOTO_LABELS,
            photo__isnull=False,
        )
    )


def unanalyzed_count(user):
    """How many of the user's raster images are still waiting for a Photo row.

    Quarantined files never get one, so counting them would announce an
    analysis that is not coming.
    """
    return (
        exclude_blocked(
            FileService.user_files_qs(user).filter(
                node_type=File.NodeType.FILE,
                type__in=PHOTO_LABELS,
                photo__isnull=True,
            )
        )
        .exclude(content="")
        .exclude(content__isnull=True)
        .count()
    )


def library_tags(user):
    """The user's tags carried by at least one photo, with ``photo_count``."""
    return (
        Tag.objects.filter(
            owner=user,
            file_tags__file__in=library_files(user).values("pk"),
        )
        .annotate(photo_count=Count("file_tags"))
        .order_by(Lower("name"))
    )
