"""Thumbnail generation service for image files."""

import logging
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)

# Raster image MIME types that Pillow can handle
THUMBNAIL_MIME_TYPES = frozenset({
    'image/jpeg',
    'image/png',
    'image/webp',
    'image/bmp',
    'image/tiff',
    'image/gif',
})

THUMBNAIL_MAX_SIZE = (512, 512)
THUMBNAIL_QUALITY = 80
THUMBNAIL_FORMAT = 'WEBP'


def get_thumbnail_path(uuid):
    """Return the storage-relative path for a file's thumbnail."""
    return f'thumbnails/{uuid}.webp'


def can_generate_thumbnail(mime_type):
    """Check if a thumbnail can be generated for the given MIME type."""
    return mime_type in THUMBNAIL_MIME_TYPES


def generate_thumbnail(file_obj):
    """Generate a WebP thumbnail for the given File instance.

    Returns True if the thumbnail was successfully created, False otherwise.
    """
    from PIL import Image, ImageOps

    if not file_obj.content or not can_generate_thumbnail(file_obj.mime_type):
        return False

    try:
        file_obj.content.open('rb')
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

        return True
    except Exception:
        logger.warning("Failed to generate thumbnail for %s", file_obj.uuid, exc_info=True)
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
