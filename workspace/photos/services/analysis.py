"""Turn photo and video files into MediaItem rows."""

import dataclasses
import logging

from django.db.models import F, Q
from django.utils import timezone

from workspace.common.logging import scrub
from workspace.files.models import File
from workspace.files.services import ffmpeg
from workspace.files.services.scanning.policy import exclude_blocked, is_blocked
from workspace.files.services.thumbnails.generation import (
    RASTER_LABELS,
    VIDEO_LABELS,
)
from workspace.files.ui.viewers import AudioViewer
from workspace.users.services.settings import get_user_timezone

from ..models import MediaItem
from . import video
from .exif import PhotoMetadata, read_metadata

logger = logging.getLogger(__name__)

# The content labels the library reads. SVG is left out on purpose: a vector
# drawing has no capture date and no fixed size.
MEDIA_LABELS = RASTER_LABELS | VIDEO_LABELS

# The version of the reader each kind of row is written by. Raise a kind's
# number when its reader starts filling a new field: rows written by an older
# version count as pending again, so existing libraries fill in on their own,
# without a manual --reanalyze (at CATCH_UP_LIMIT files per hourly pass, see
# tasks.py, so a large library takes several passes).
ANALYSIS_VERSIONS = {
    # 2: lens, exposure settings and GPS position.
    MediaItem.MediaType.PHOTO: 2,
    MediaItem.MediaType.VIDEO: 1,
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

# Rows fetched per keyset page by analyze_pending. A read cursor held open
# across the write transactions of the loop is what makes SQLite raise
# "database is locked", so the selection is paged rather than streamed.
_PAGE_SIZE = 200


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
    # Both content exclusions are needed: Django renders exclude(content="")
    # as NOT (content = '' AND content IS NOT NULL), which keeps NULL rows.
    qs = exclude_blocked(
        library_candidates(File.objects.filter(deleted_at__isnull=True))
        .exclude(content="")
        .exclude(content__isnull=True)
    )
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

    current_hash = (
        File.objects.filter(pk=file_obj.pk)
        .values_list("content_hash", flat=True)
        .first()
    )
    if current_hash is None or current_hash != read_hash:
        return None

    item, _ = MediaItem.objects.update_or_create(
        file_id=file_obj.pk,
        defaults={
            **_EMPTY_METADATA,
            **dataclasses.asdict(metadata),
            "media_type": media_type,
            "content_hash": read_hash,
            "analysis_version": ANALYSIS_VERSIONS[media_type],
            "analyzed_at": timezone.now(),
        },
    )
    return item


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


def pending_media_ids(*, reanalyze=False, limit=None):
    """Yield the uuids of pending files, paged by keyset."""
    queryset = pending_media_qs(reanalyze=reanalyze)
    produced = 0
    last_uuid = None
    while True:
        page_qs = queryset.order_by("uuid")
        if last_uuid is not None:
            page_qs = page_qs.filter(uuid__gt=last_uuid)
        page = list(page_qs.values_list("uuid", flat=True)[:_PAGE_SIZE])
        if not page:
            return
        last_uuid = page[-1]
        for uuid in page:
            if limit is not None and produced >= limit:
                return
            yield uuid
            produced += 1


def analyze_pending(*, limit=None):
    """Analyze every pending file inline; return counts for the logs."""
    stats = {"analyzed": 0, "skipped": 0}
    for uuid in pending_media_ids(limit=limit):
        file_obj = File.objects.select_related("owner").filter(uuid=uuid).first()
        if file_obj is not None and analyze_media(file_obj) is not None:
            stats["analyzed"] += 1
        else:
            stats["skipped"] += 1
    return stats
