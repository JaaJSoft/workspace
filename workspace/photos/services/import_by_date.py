"""Filing photos imported from the Photos page into year and month folders.

With the `photos` / `import_by_date` setting on, the page reports what it
just uploaded to the import folder, and each photo moves to
``<import folder>/<YYYY>/<MM>`` after the day it was taken, in the owner's
timezone. Only those uploads: a file a phone backup app puts there stays
where the app put it, or the app would see it vanish and send it again.

A photo with no capture date stays at the top of the import folder.
"""

import logging

from django.db import transaction
from django.utils import timezone

from workspace.common.logging import scrub
from workspace.files.models import File
from workspace.files.services import FileService
from workspace.users.services.settings import get_user_timezone

from ..models import MediaItem
from .analysis import analyze_media, is_media_candidate
from .import_folder import import_folder
from .preferences import import_by_date

logger = logging.getLogger(__name__)

# Uploads one report may name: a larger import comes in several reports.
MAX_FILES = 500


def importable_files(user, file_uuids):
    """The files of *file_uuids* sitting at the top of *user*'s import folder."""
    folder = import_folder(user)
    if folder is None:
        return File.objects.none()
    return File.objects.alive().filter(
        uuid__in=file_uuids,
        parent=folder,
        owner=user,
        node_type=File.NodeType.FILE,
    )


def file_by_date(user, file_uuids):
    """Move each of *file_uuids* still at the top of the import folder into
    its year and month folder. Returns how many moved."""
    if not import_by_date(user):
        return 0
    tz = get_user_timezone(user)
    moved = 0
    for file_obj in importable_files(user, file_uuids).select_related("parent"):
        taken_at = _taken_at(file_obj)
        if taken_at is None:
            continue
        day = timezone.localtime(taken_at, tz)
        try:
            _move_to_month(user, file_obj, day.year, day.month)
        except ValueError as exc:
            # A file named like the year or month folder, or a move the
            # files rules refuse: the photo stays in the import folder.
            logger.info(
                "Could not file %s by date: %s", scrub(file_obj.name), scrub(str(exc))
            )
            continue
        moved += 1
    return moved


def _taken_at(file_obj):
    """The capture time of *file_obj*, reading its metadata when the upload's
    own analysis has not run yet."""
    if not is_media_candidate(file_obj):
        return None
    item = MediaItem.objects.filter(
        file=file_obj, content_hash=file_obj.content_hash
    ).first()
    if item is None:
        item = analyze_media(file_obj)
    return item.taken_at if item is not None else None


@transaction.atomic
def _move_to_month(user, file_obj, year, month):
    # Locked so that two reports filing into the same new month create one
    # folder, not two with the same name.
    root = File.objects.select_for_update().get(pk=file_obj.parent_id)
    year_folder = _child_folder(user, root, f"{year:04d}")
    month_folder = _child_folder(user, year_folder, f"{month:02d}")
    if FileService.find_name_conflict(
        file_obj.owner, month_folder, file_obj.name, exclude_pk=file_obj.pk
    ):
        # Cameras reuse names (IMG_0001.JPG): keep both, as the import did.
        new_name = FileService.available_file_name(
            file_obj.owner, month_folder, file_obj.name, avoiding=(root,)
        )
        FileService.rename(file_obj, new_name, acting_user=user)
    FileService.move(file_obj, month_folder, acting_user=user)


def _child_folder(user, parent, name):
    existing = (
        File.objects.alive()
        .filter(parent=parent, node_type=File.NodeType.FOLDER, name__iexact=name)
        .first()
    )
    if existing is not None:
        return existing
    return FileService.create_folder(user, name, parent=parent, acting_user=user)
