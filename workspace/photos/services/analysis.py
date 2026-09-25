"""Turn raster image files into Photo rows."""

import logging

from django.db.models import F, Q
from django.utils import timezone

from workspace.common.logging import scrub
from workspace.files.models import File
from workspace.files.services.scanning.policy import exclude_blocked
from workspace.files.services.thumbnails.generation import RASTER_LABELS
from workspace.users.services.settings import get_user_timezone

from ..models import Photo
from .exif import PhotoMetadata, read_metadata

logger = logging.getLogger(__name__)

# The content labels the library reads. SVG is left out on purpose: a vector
# drawing has no capture date and no fixed size.
PHOTO_LABELS = RASTER_LABELS

# Rows fetched per keyset page by analyze_pending. A read cursor held open
# across the write transactions of the loop is what makes SQLite raise
# "database is locked", so the selection is paged rather than streamed.
_PAGE_SIZE = 200


def is_photo_candidate(file_obj):
    """True when *file_obj* is a raster image with bytes to read."""
    return (
        file_obj.node_type == File.NodeType.FILE
        and file_obj.type in PHOTO_LABELS
        and bool(file_obj.content)
    )


def pending_photos_qs(*, reanalyze=False):
    """Live raster files whose Photo row is missing or describes older bytes.

    A file whose own hash is empty (registered before hashes existed) only
    counts when it has no row at all: comparing an empty hash would mark it
    stale on every pass, forever.

    Quarantined files are left out: reading their bytes is exactly what the
    malware policy forbids, and they are hidden from the timeline anyway.
    """
    # Both content exclusions are needed: Django renders exclude(content="")
    # as NOT (content = '' AND content IS NOT NULL), which keeps NULL rows.
    qs = exclude_blocked(
        File.objects.filter(
            node_type=File.NodeType.FILE,
            deleted_at__isnull=True,
            type__in=PHOTO_LABELS,
        )
        .exclude(content="")
        .exclude(content__isnull=True)
    )
    if reanalyze:
        return qs
    return qs.filter(
        Q(photo__isnull=True)
        | (~Q(content_hash="") & ~Q(photo__content_hash=F("content_hash")))
    )


def analyze_photo(file_obj):
    """Read *file_obj*'s metadata into its Photo row; return the row or None.

    None means nothing was written: the file is not a raster image, its blob
    could not be opened (the catch-up task tries again), or its content was
    replaced while it was being read (the replacement's own event takes
    over). An image whose bytes Pillow cannot make sense of still gets a row,
    all nulls: it has been looked at, and it belongs in the Undated bucket
    rather than in front of every hourly pass.
    """
    if not is_photo_candidate(file_obj):
        return None

    read_hash = file_obj.content_hash
    try:
        handle = file_obj.content.open("rb")
    except OSError as exc:
        logger.warning(
            "Photo analysis cannot read the blob of %s: %s",
            scrub(file_obj.content.name),
            scrub(str(exc)),
        )
        return None
    try:
        metadata = read_metadata(handle, default_tz=get_user_timezone(file_obj.owner))
    except Exception:
        logger.info(
            "Photo analysis could not decode %s",
            scrub(file_obj.content.name),
            exc_info=True,
        )
        metadata = PhotoMetadata()
    finally:
        handle.close()

    current_hash = (
        File.objects.filter(pk=file_obj.pk)
        .values_list("content_hash", flat=True)
        .first()
    )
    if current_hash is None or current_hash != read_hash:
        return None

    photo, _ = Photo.objects.update_or_create(
        file_id=file_obj.pk,
        defaults={
            "taken_at": metadata.taken_at,
            "width": metadata.width,
            "height": metadata.height,
            "camera_make": metadata.camera_make,
            "camera_model": metadata.camera_model,
            "content_hash": read_hash,
            "analyzed_at": timezone.now(),
        },
    )
    return photo


def forget_photo(file_obj):
    """Drop the row of a file that stopped being a raster image."""
    Photo.objects.filter(file_id=file_obj.pk).delete()


def pending_photo_ids(*, reanalyze=False, limit=None):
    """Yield the uuids of pending files, paged by keyset."""
    queryset = pending_photos_qs(reanalyze=reanalyze)
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
    for uuid in pending_photo_ids(limit=limit):
        file_obj = File.objects.select_related("owner").filter(uuid=uuid).first()
        if file_obj is not None and analyze_photo(file_obj) is not None:
            stats["analyzed"] += 1
        else:
            stats["skipped"] += 1
    return stats
