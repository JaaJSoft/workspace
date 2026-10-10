"""Thumbnail generation service for image and video files."""

import logging
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from ...metrics import FILES_THUMBNAIL_DURATION, FILES_THUMBNAIL_RESULT
from .. import ffmpeg
from ..raster_formats import RASTER_LABELS
from ..scanning.policy import is_blocked
from .poster import poster_frame

logger = logging.getLogger(__name__)

_SVG_LABELS = frozenset({"svg"})
# The containers phones and cameras record into. Magika also files flv, wmv
# and mpegts under "video": mpegts claims the .tsv extension.
VIDEO_LABELS = frozenset({"mp4", "qt", "webm", "mkv", "3gp", "avi"})
THUMBNAIL_LABELS = RASTER_LABELS | _SVG_LABELS | VIDEO_LABELS

# Whitelist of labels used as the `mime_family` metric label.
# Anything not in this set is reported as 'other' so the label cardinality
# stays bounded even if THUMBNAIL_LABELS is later widened by mistake.
_KNOWN_LABELS = RASTER_LABELS | _SVG_LABELS | VIDEO_LABELS


def _label_family(content_label):
    """Return a bounded label value for the metric ('jpeg', 'png', ..., 'other')."""
    if not content_label:
        return "unknown"
    return content_label if content_label in _KNOWN_LABELS else "other"


THUMBNAIL_MAX_SIZE = (512, 512)
THUMBNAIL_QUALITY = 80
THUMBNAIL_FORMAT = "WEBP"
# Smaller renditions of the same thumbnail, for grids whose tiles are far
# smaller than THUMBNAIL_MAX_SIZE: decoding and scaling a 512px image per tile
# is what makes a large grid stutter while it scrolls.
THUMBNAIL_VARIANT_SIZES = (128, 256)
THUMBNAIL_SIZES = (*THUMBNAIL_VARIANT_SIZES, THUMBNAIL_MAX_SIZE[0])


def get_thumbnail_path(uuid, size=THUMBNAIL_MAX_SIZE[0]):
    """Return the storage-relative path for a file's thumbnail at *size*."""
    if size == THUMBNAIL_MAX_SIZE[0]:
        return f"thumbnails/{uuid}.webp"
    return f"thumbnails/{uuid}_{size}.webp"


def _encode(img):
    buf = BytesIO()
    img.save(buf, format=THUMBNAIL_FORMAT, quality=THUMBNAIL_QUALITY)
    return buf.getvalue()


def _variant(img, size):
    from PIL import Image

    small = img.copy()
    small.thumbnail((size, size), Image.LANCZOS)
    return small


def _store(path, data):
    if default_storage.exists(path):
        default_storage.delete(path)
    default_storage.save(path, ContentFile(data))


def parse_thumbnail_size(value):
    """A ``?size=`` query value as one of THUMBNAIL_SIZES, or None when invalid.

    No value asks for the full-size thumbnail.
    """
    if not value:
        return THUMBNAIL_MAX_SIZE[0]
    try:
        size = int(value)
    except ValueError:
        return None
    return size if size in THUMBNAIL_SIZES else None


def thumbnail_variant_path(uuid, size):
    """The storage path of *uuid*'s thumbnail at *size*, or None without one.

    A variant missing next to an existing full-size thumbnail (one generated
    before the variants existed) is derived from it on the spot and stored.
    """
    path = get_thumbnail_path(uuid, size)
    if default_storage.exists(path):
        return path
    base_path = get_thumbnail_path(uuid)
    if size == THUMBNAIL_MAX_SIZE[0] or not default_storage.exists(base_path):
        return None

    from PIL import Image

    try:
        with default_storage.open(base_path, "rb") as base:
            img = Image.open(base)
            img.load()
    except OSError:
        logger.warning("Unreadable thumbnail for %s", uuid, exc_info=True)
        return None
    saved = default_storage.save(path, ContentFile(_encode(_variant(img, size))))
    # Two requests deriving the same variant at once: the storage hands the
    # loser an alternative name, which nothing would ever read.
    if saved != path:
        default_storage.delete(saved)
    return path


def generatable_labels():
    """The labels a thumbnail can be generated for on this deployment.

    Videos need ffmpeg for their poster frame; without it they are left out
    rather than failing, and parking, on every pass.
    """
    if ffmpeg.FFMPEG:
        return THUMBNAIL_LABELS
    return THUMBNAIL_LABELS - VIDEO_LABELS


def can_generate_thumbnail(content_label):
    """Check if a thumbnail can be generated for the given content label."""
    return content_label in generatable_labels()


def _audio_viewer_slug():
    from workspace.files.ui.viewers import AudioViewer

    return AudioViewer.slug


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

    Returns True when the thumbnail was created, False when the file is not
    one to generate a thumbnail for. A file that cannot be decoded raises: the
    processor runner counts it against the file's budget.
    """
    from PIL import Image, ImageOps

    # A video container pinned to the audio player holds a recording with no
    # picture (see filetype.pin_viewer_for_upload). A quarantined file is
    # never read at all: decoding untrusted bytes is what the policy forbids.
    if (
        not file_obj.content
        or not can_generate_thumbnail(file_obj.type)
        or (file_obj.type in VIDEO_LABELS and file_obj.viewer == _audio_viewer_slug())
        or is_blocked(file_obj)
    ):
        FILES_THUMBNAIL_RESULT.labels(result="skipped").inc()
        return False

    family = _label_family(file_obj.type)
    try:
        with FILES_THUMBNAIL_DURATION.labels(mime_family=family).time():
            if file_obj.type in VIDEO_LABELS:
                img = poster_frame(file_obj, THUMBNAIL_MAX_SIZE)
            elif file_obj.type in _SVG_LABELS:
                file_obj.content.open("rb")
                svg_data = file_obj.content.read()
                img = _rasterize_svg(svg_data)
            else:
                file_obj.content.open("rb")
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

            _store(get_thumbnail_path(file_obj.uuid), _encode(img))
            for size in THUMBNAIL_VARIANT_SIZES:
                _store(
                    get_thumbnail_path(file_obj.uuid, size),
                    _encode(_variant(img, size)),
                )

        FILES_THUMBNAIL_RESULT.labels(result="success").inc()
    except Exception:
        FILES_THUMBNAIL_RESULT.labels(result="failed").inc()
        raise
    finally:
        # Best-effort cleanup: a close() that fails after the body has been
        # processed (or failed) is not actionable; we drop it at debug level.
        try:
            file_obj.content.close()
        except Exception:
            logger.debug("Failed to close content for %s", file_obj.uuid, exc_info=True)

    return True


def delete_thumbnail(uuid):
    """Delete the thumbnail files for the given UUID, every size."""
    try:
        for size in THUMBNAIL_SIZES:
            thumb_path = get_thumbnail_path(uuid, size)
            if default_storage.exists(thumb_path):
                default_storage.delete(thumb_path)
    except Exception:
        logger.warning("Failed to delete thumbnail for %s", uuid, exc_info=True)


def pending_thumbnails_qs(*, reanalyze=False):
    """Live files a thumbnail can be generated for and that have none yet.

    With *reanalyze*, every live file a thumbnail can be generated for, with a
    thumbnail or not.
    """
    from workspace.files.models import File
    from workspace.files.services.scanning.policy import exclude_blocked

    # Quarantining a file deletes its thumbnail, and "no thumbnail" is exactly
    # what this looks for - so without the exclusion the preview of an
    # infected image reappears at the next pass. It also means a file leaving
    # quarantine becomes pending again on its own.
    qs = exclude_blocked(
        File.objects.alive()
        .with_blob()
        .filter(type__in=generatable_labels())
        .exclude(type__in=VIDEO_LABELS, viewer=_audio_viewer_slug())
    )
    if reanalyze:
        return qs
    return qs.filter(has_thumbnail=False)


def is_thumbnail_candidate(file_obj):
    """True when the upload pipeline should try a thumbnail for *file_obj*."""
    return bool(file_obj.content) and can_generate_thumbnail(file_obj.type)


def refresh_thumbnail(file_obj):
    """Generate *file_obj*'s thumbnail and flag the row; True on success.

    Raises when the file cannot be decoded, like generate_thumbnail.
    """
    if not generate_thumbnail(file_obj):
        return False
    if not file_obj.has_thumbnail:
        file_obj.has_thumbnail = True
        file_obj.save(update_fields=["has_thumbnail"])
    return True
