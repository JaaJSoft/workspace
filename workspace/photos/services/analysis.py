"""Turn photo and video files into MediaItem rows."""

import dataclasses
import logging

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from workspace.common.logging import scrub
from workspace.files.models import File
from workspace.files.services import ffmpeg
from workspace.files.services.detection import get_label_info
from workspace.files.services.raster_formats import RASTER_LABELS
from workspace.files.services.scanning.policy import exclude_blocked, is_blocked
from workspace.files.services.thumbnails.generation import VIDEO_LABELS
from workspace.files.ui.viewers import AudioViewer
from workspace.users.services.settings import get_user_timezone

from ..models import MediaItem
from . import video
from .exif import PhotoMetadata, read_metadata
from .filename_dates import date_from_filename

logger = logging.getLogger(__name__)

# The content labels the library reads. SVG is left out on purpose: a vector
# drawing has no capture date and no fixed size.
MEDIA_LABELS = RASTER_LABELS | VIDEO_LABELS


def library_extensions():
    """The file extensions of every format the library reads, which the
    import picker and drop filter go by: browsers give no MIME type to a
    HEIC or a camera RAW."""
    return sorted(
        {
            extension
            for label in MEDIA_LABELS
            for extension in get_label_info(label).get("extensions", [])
        }
    )


# The version of the reader each kind of row is written by. Raise a kind's
# number when its reader starts filling a new field: rows written by an older
# version count as pending again, so existing libraries fill in on their own,
# without a manual --reanalyze (at CATCH_UP_LIMIT files per hourly pass, see
# files/tasks.py, so a very large library takes several passes).
ANALYSIS_VERSIONS = {
    # 2: lens, exposure settings and GPS position.
    # 3: the date written in the file name when the EXIF has none.
    MediaItem.MediaType.PHOTO: 3,
    # 2: the date written in the file name when the container has none.
    MediaItem.MediaType.VIDEO: 2,
}
# The version of a row written before rows recorded one.
_UNVERSIONED = 1

# Every field a reader fills, empty. A row is rewritten from this base, so a
# photo replaced by a video (or the other way round) keeps nothing of the
# previous reader's values.
_EMPTY_METADATA = {
    **dataclasses.asdict(PhotoMetadata()),
    **dataclasses.asdict(video.VideoMetadata()),
}

# What a correction by hand overrides (services/capture_details.py): a new
# reading of the file leaves these alone while the matching flag is set.
_DATE_FIELDS = ("taken_at", "taken_at_from_name")
_LOCATION_FIELDS = ("latitude", "longitude", "altitude")


def library_candidates(files):
    """The photos and videos among *files*.

    A video container pinned to the audio player is a recording with no
    picture (see ``filetype.pin_viewer_for_upload``), not a video.
    """
    return files.filter(node_type=File.NodeType.FILE, type__in=MEDIA_LABELS).exclude(
        type__in=VIDEO_LABELS, viewer=AudioViewer.slug
    )


def media_type_for(file_obj):
    """The kind of library item *file_obj* is, or None when it is not one."""
    if file_obj.node_type != File.NodeType.FILE or not file_obj.content:
        return None
    if file_obj.type in RASTER_LABELS:
        return MediaItem.MediaType.PHOTO
    if file_obj.type in VIDEO_LABELS and file_obj.viewer != AudioViewer.slug:
        return MediaItem.MediaType.VIDEO
    return None


def is_media_candidate(file_obj):
    """True when *file_obj* is a photo or a video the library may read.

    A quarantined file never is: reading its bytes is exactly what the malware
    policy forbids.
    """
    return media_type_for(file_obj) is not None and not is_blocked(file_obj)


def pending_media_qs(*, reanalyze=False):
    """Live photos and videos whose MediaItem row is missing, describes
    older bytes, or was written by an older version of the reader.

    A file whose own hash is empty (registered before hashes existed) only
    counts when it has no row at all: comparing an empty hash would mark it
    stale on every pass, forever.

    Quarantined files are left out: reading their bytes is exactly what the
    malware policy forbids, and they are hidden from the timeline anyway.
    """
    qs = exclude_blocked(library_candidates(File.objects.alive().with_blob()))
    if reanalyze:
        return qs
    pending = Q(media_item__isnull=True) | (
        ~Q(content_hash="") & ~Q(media_item__content_hash=F("content_hash"))
    )
    for media_type, version in ANALYSIS_VERSIONS.items():
        if version > _UNVERSIONED:
            pending |= Q(media_item__media_type=media_type) & (
                Q(media_item__analysis_version__isnull=True)
                | Q(media_item__analysis_version__lt=version)
            )
    return qs.filter(pending)


def analyze_media(file_obj):
    """Read *file_obj*'s metadata into its MediaItem row; return the row or None.

    None means nothing was written: the file is not a photo or a video, its
    blob could not be opened (the catch-up task tries again), or its content
    was replaced while it was being read (the replacement's own event takes
    over). A file whose bytes cannot be made sense of still gets a row, all
    nulls: it has been looked at, and it belongs in the Undated bucket rather
    than in front of every hourly pass. So does a video on a deployment
    without ffprobe.

    A file whose metadata carries no date is dated by its name when the name
    holds one.
    """
    if not is_media_candidate(file_obj):
        return None
    media_type = media_type_for(file_obj)

    read_hash = file_obj.content_hash
    default_tz = get_user_timezone(file_obj.owner)
    if media_type == MediaItem.MediaType.VIDEO:
        metadata = _read_video(file_obj, default_tz)
    else:
        metadata = _read_photo(file_obj, default_tz)
    if metadata is None:
        return None

    # The file row stays locked until the row below is written: a rename
    # either landed before and its name is read here, or waits and its own
    # handler re-dates the row this writes. Reading file_obj.name instead
    # would let a name from before the rename win.
    with transaction.atomic():
        current = (
            File.objects.select_for_update()
            .filter(pk=file_obj.pk)
            .values_list("content_hash", "name")
            .first()
        )
        if current is None or current[0] != read_hash:
            return None
        current_name = current[1]

        taken_at = metadata.taken_at
        if taken_at is None:
            taken_at = date_from_filename(current_name, default_tz=default_tz)
            taken_at_from_name = taken_at is not None
        else:
            taken_at_from_name = False

        defaults = {
            **_EMPTY_METADATA,
            **dataclasses.asdict(metadata),
            "taken_at": taken_at,
            "taken_at_from_name": taken_at_from_name,
            "media_type": media_type,
            "content_hash": read_hash,
            "analysis_version": ANALYSIS_VERSIONS[media_type],
            "analyzed_at": timezone.now(),
        }
        # Locked with the file row: a correction landing now waits, and one
        # that landed before is kept.
        by_user = (
            MediaItem.objects.select_for_update()
            .filter(file_id=file_obj.pk)
            .values("taken_at_by_user", "location_by_user")
            .first()
        )
        if by_user and by_user["taken_at_by_user"]:
            for field in _DATE_FIELDS:
                defaults.pop(field)
        if by_user and by_user["location_by_user"]:
            for field in _LOCATION_FIELDS:
                defaults.pop(field)
        item, _ = MediaItem.objects.update_or_create(
            file_id=file_obj.pk, defaults=defaults
        )
    return item


def redate_from_name(file_obj):
    """Date *file_obj*'s row from its current name, unless its metadata
    dated it.

    Renaming an undated scan to "1998-07-12 Mariage.jpg" files it on that
    day, and renaming a photo dated by its name moves it with the name (or
    back to Undated). Only the name is read: the bytes have not changed.
    """
    taken_at = date_from_filename(
        file_obj.name, default_tz=get_user_timezone(file_obj.owner)
    )
    MediaItem.objects.filter(
        Q(taken_at__isnull=True) | Q(taken_at_from_name=True), file_id=file_obj.pk
    ).update(taken_at=taken_at, taken_at_from_name=taken_at is not None)


def refresh_media_item(file_obj):
    """Analyze *file_obj* for the catch-up; True when its row was written."""
    return analyze_media(file_obj) is not None


def _cannot_read(file_obj, exc):
    logger.warning(
        "Media analysis cannot read the blob of %s: %s",
        scrub(file_obj.content.name),
        scrub(str(exc)),
    )


def _read_photo(file_obj, default_tz):
    try:
        handle = file_obj.content.open("rb")
    except OSError as exc:
        _cannot_read(file_obj, exc)
        return None
    try:
        return read_metadata(handle, default_tz=default_tz)
    except Exception:
        logger.info(
            "Media analysis could not decode %s",
            scrub(file_obj.content.name),
            exc_info=True,
        )
        return PhotoMetadata()
    finally:
        handle.close()


def _read_video(file_obj, default_tz):
    try:
        with ffmpeg.local_path(file_obj.content) as path:
            return video.read_metadata(path, default_tz=default_tz)
    except ffmpeg.MediaToolError as exc:
        logger.info(
            "Media analysis could not probe %s: %s",
            scrub(file_obj.content.name),
            scrub(str(exc)),
        )
        return video.VideoMetadata()
    except OSError as exc:
        _cannot_read(file_obj, exc)
        return None


def forget_media(file_obj):
    """Drop the row of a file that stopped being a photo or a video."""
    MediaItem.objects.filter(file_id=file_obj.pk).delete()
