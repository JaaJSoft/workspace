"""Thumbnail generation service for image files."""

import logging
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from ..metrics import FILES_THUMBNAIL_DURATION, FILES_THUMBNAIL_RESULT

logger = logging.getLogger(__name__)

# Whitelist of MIME subtypes used as the `mime_family` metric label.
# Anything not in this set is reported as 'other' so the label cardinality
# stays bounded even if THUMBNAIL_MIME_TYPES is later widened by mistake.
_KNOWN_MIME_FAMILIES = frozenset({'jpeg', 'png', 'webp', 'bmp', 'tiff', 'gif', 'svg'})


def _mime_family(mime_type):
    """Return a bounded label value for the metric ('jpeg', 'png', ..., 'other')."""
    if not mime_type or '/' not in mime_type:
        return 'unknown'
    subtype = mime_type.split('/', 1)[1].lower().split('+', 1)[0] or 'unknown'
    if subtype == 'unknown':
        return 'unknown'
    return subtype if subtype in _KNOWN_MIME_FAMILIES else 'other'

# Raster image MIME types that Pillow can handle
_RASTER_MIME_TYPES = frozenset({
    'image/jpeg',
    'image/png',
    'image/webp',
    'image/bmp',
    'image/tiff',
    'image/gif',
})

# SVG MIME types (rasterized via cairosvg before Pillow processing)
_SVG_MIME_TYPES = frozenset({
    'image/svg+xml',
})

THUMBNAIL_MIME_TYPES = _RASTER_MIME_TYPES | _SVG_MIME_TYPES

THUMBNAIL_MAX_SIZE = (512, 512)
THUMBNAIL_QUALITY = 80
THUMBNAIL_FORMAT = 'WEBP'


def get_thumbnail_path(uuid):
    """Return the storage-relative path for a file's thumbnail."""
    return f'thumbnails/{uuid}.webp'


def can_generate_thumbnail(mime_type):
    """Check if a thumbnail can be generated for the given MIME type."""
    return mime_type in THUMBNAIL_MIME_TYPES


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

    if not file_obj.content or not can_generate_thumbnail(file_obj.mime_type):
        FILES_THUMBNAIL_RESULT.labels(result='skipped').inc()
        return False

    family = _mime_family(file_obj.mime_type)
    try:
        with FILES_THUMBNAIL_DURATION.labels(mime_family=family).time():
            file_obj.content.open('rb')

            if file_obj.mime_type in _SVG_MIME_TYPES:
                svg_data = file_obj.content.read()
                img = _rasterize_svg(svg_data)
            else:
                img = Image.open(file_obj.content)

                # Auto-rotate based on EXIF orientation
                try:
                    img = ImageOps.exif_transpose(img)
                except Exception:
                    pass

            # Convert to RGB for WebP output
            if img.mode in ('RGBA', 'LA', 'PA', 'P'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                alpha = img.split()[-1] if img.mode.endswith('A') else None
                background.paste(img, mask=alpha)
                img = background
            elif img.mode != 'RGB':
                img = img.convert('RGB')

            img.thumbnail(THUMBNAIL_MAX_SIZE, Image.LANCZOS)

            buf = BytesIO()
            img.save(buf, format=THUMBNAIL_FORMAT, quality=THUMBNAIL_QUALITY)
            buf.seek(0)

            thumb_path = get_thumbnail_path(file_obj.uuid)
            if default_storage.exists(thumb_path):
                default_storage.delete(thumb_path)
            default_storage.save(thumb_path, ContentFile(buf.read()))

        FILES_THUMBNAIL_RESULT.labels(result='success').inc()
        return True
    except Exception:
        logger.warning("Failed to generate thumbnail for %s", file_obj.uuid, exc_info=True)
        FILES_THUMBNAIL_RESULT.labels(result='failed').inc()
        return False
    finally:
        try:
            file_obj.content.close()
        except Exception:
            pass


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

    qs = File.objects.filter(
        node_type=File.NodeType.FILE,
        has_thumbnail=False,
        deleted_at__isnull=True,
        mime_type__in=THUMBNAIL_MIME_TYPES,
    ).exclude(content='').exclude(content__isnull=True)

    stats = {'generated': 0, 'failed': 0, 'total': 0}

    for file_obj in qs.iterator():
        stats['total'] += 1
        if generate_thumbnail(file_obj):
            file_obj.has_thumbnail = True
            file_obj.save(update_fields=['has_thumbnail'])
            stats['generated'] += 1
        else:
            stats['failed'] += 1

    return stats
