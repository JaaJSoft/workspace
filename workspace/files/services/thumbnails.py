"""Thumbnail generation service for image files."""

import logging
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from ..metrics import FILES_THUMBNAIL_DURATION, FILES_THUMBNAIL_RESULT

logger = logging.getLogger(__name__)

# Whitelist of labels used as the `mime_family` metric label.
# Anything not in this set is reported as 'other' so the label cardinality
# stays bounded even if THUMBNAIL_LABELS is later widened by mistake.
_KNOWN_IMAGE_LABELS = frozenset({"jpeg", "png", "webp", "bmp", "tiff", "gif", "svg"})


def _label_family(content_label):
    """Return a bounded label value for the metric ('jpeg', 'png', ..., 'other')."""
    if not content_label:
        return "unknown"
    return content_label if content_label in _KNOWN_IMAGE_LABELS else "other"


_RASTER_LABELS = frozenset({"jpeg", "png", "webp", "bmp", "tiff", "gif"})
_SVG_LABELS = frozenset({"svg"})
THUMBNAIL_LABELS = _RASTER_LABELS | _SVG_LABELS

THUMBNAIL_MAX_SIZE = (512, 512)
THUMBNAIL_QUALITY = 80
THUMBNAIL_FORMAT = "WEBP"


def get_thumbnail_path(uuid):
    """Return the storage-relative path for a file's thumbnail."""
    return f"thumbnails/{uuid}.webp"


def can_generate_thumbnail(content_label):
    """Check if a thumbnail can be generated for the given content label."""
    return content_label in THUMBNAIL_LABELS


def _rasterize_svg(svg_data):
    """Convert SVG bytes to a Pillow Image via cairosvg.

    Renders the SVG at a size that fits within THUMBNAIL_MAX_SIZE while
    preserving the aspect ratio.
    """
    import cairosvg
    from PIL import Image

    png_data = cairosvg.svg2png(
        bytestring=svg_data,
        output_width=THUMBNAIL_MAX_SIZE[0],
        output_height=THUMBNAIL_MAX_SIZE[1],
    )
    return Image.open(BytesIO(png_data))


def generate_thumbnail(file_obj):
    """Generate a WebP thumbnail for the given File instance.

    Returns True if the thumbnail was successfully created, False otherwise.
    """
    from PIL import Image, ImageOps

    if not file_obj.content or not can_generate_thumbnail(file_obj.type):
        FILES_THUMBNAIL_RESULT.labels(result="skipped").inc()
        return False

    family = _label_family(file_obj.type)
    try:
        with FILES_THUMBNAIL_DURATION.labels(mime_family=family).time():
            file_obj.content.open("rb")

            if file_obj.type in _SVG_LABELS:
                svg_data = file_obj.content.read()
                img = _rasterize_svg(svg_data)
            else:
                img = Image.open(file_obj.content)

                # Hint the decoder to load at a reduced scale near the target
                # size, before any pixels are read. For JPEG this makes libjpeg
                # decode at 1/2, 1/4 or 1/8 scale - a large CPU and memory win
                # on big photos - and is a no-op for formats without draft
                # support. Kept before exif_transpose (which forces a full
                # load) so the reduced-scale decode actually takes effect.
                img.draft(None, THUMBNAIL_MAX_SIZE)

                # Auto-rotate based on EXIF orientation. A malformed EXIF
                # block must not abort thumbnail generation; we log at debug
                # level for diagnosability and continue with the un-rotated image.
                try:
                    img = ImageOps.exif_transpose(img)
                except Exception:
                    logger.debug(
                        "EXIF transpose failed for %s, continuing with un-rotated image",
                        file_obj.uuid,
                        exc_info=True,
                    )

            # Convert to RGB for WebP output
            if img.mode in ("RGBA", "LA", "PA", "P"):
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                alpha = img.split()[-1] if img.mode.endswith("A") else None
                background.paste(img, mask=alpha)
                img = background
            elif img.mode != "RGB":
                img = img.convert("RGB")

            img.thumbnail(THUMBNAIL_MAX_SIZE, Image.LANCZOS)

            buf = BytesIO()
            img.save(buf, format=THUMBNAIL_FORMAT, quality=THUMBNAIL_QUALITY)
            buf.seek(0)

            thumb_path = get_thumbnail_path(file_obj.uuid)
            if default_storage.exists(thumb_path):
                default_storage.delete(thumb_path)
            default_storage.save(thumb_path, ContentFile(buf.read()))

        FILES_THUMBNAIL_RESULT.labels(result="success").inc()
        return True
    except Exception:
        logger.warning(
            "Failed to generate thumbnail for %s", file_obj.uuid, exc_info=True
        )
        FILES_THUMBNAIL_RESULT.labels(result="failed").inc()
        return False
    finally:
        # Best-effort cleanup: a close() that fails after the body has been
        # processed (or failed) is not actionable; we drop it at debug level.
        try:
            file_obj.content.close()
        except Exception:
            logger.debug("Failed to close content for %s", file_obj.uuid, exc_info=True)


def delete_thumbnail(uuid):
    """Delete the thumbnail file for the given UUID, if it exists."""
    try:
        thumb_path = get_thumbnail_path(uuid)
        if default_storage.exists(thumb_path):
            default_storage.delete(thumb_path)
    except Exception:
        logger.warning("Failed to delete thumbnail for %s", uuid, exc_info=True)


def generate_missing_thumbnails():
    """Generate thumbnails for all image files that don't have one yet.

    Returns a dict with generation statistics.
    """
    from workspace.files.models import File

    qs = (
        File.objects.filter(
            node_type=File.NodeType.FILE,
            has_thumbnail=False,
            deleted_at__isnull=True,
            type__in=THUMBNAIL_LABELS,
        )
        .exclude(content="")
        .exclude(content__isnull=True)
    )

    stats = {"generated": 0, "failed": 0, "total": 0}

    for file_obj in qs.iterator():
        stats["total"] += 1
        if generate_thumbnail(file_obj):
            file_obj.has_thumbnail = True
            file_obj.save(update_fields=["has_thumbnail"])
            stats["generated"] += 1
        else:
            stats["failed"] += 1

    return stats
