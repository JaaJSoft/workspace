"""Shared image processing utilities.

Provides common functions for cropping, resizing, and saving images as WebP.
Used by both user avatar and group conversation avatar services.
"""

from __future__ import annotations

import hmac
import logging
import os
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image, ImageOps

_ETAG_SECRET = os.urandom(32)

logger = logging.getLogger(__name__)

DEFAULT_SIZE = 256
DEFAULT_QUALITY = 85


def process_image_to_webp(
    image_file,
    crop_x: float,
    crop_y: float,
    crop_w: float,
    crop_h: float,
    size: int = DEFAULT_SIZE,
    quality: int = DEFAULT_QUALITY,
) -> bytes:
    """Open *image_file*, EXIF-transpose, crop, convert to RGB, resize, and return WebP bytes."""
    img = Image.open(image_file)
    img = ImageOps.exif_transpose(img)

    left = int(crop_x)
    top = int(crop_y)
    right = int(crop_x + crop_w)
    bottom = int(crop_y + crop_h)
    img = img.crop((left, top, right, bottom))

    img = img.convert("RGB")
    img = img.resize((size, size), Image.LANCZOS)

    buf = BytesIO()
    img.save(buf, format="WEBP", quality=quality)
    return buf.getvalue()


def save_image(path: str, image_bytes: bytes) -> None:
    """Save *image_bytes* to *path* in default_storage, replacing any existing file."""
    if default_storage.exists(path):
        default_storage.delete(path)
    default_storage.save(path, ContentFile(image_bytes))


def delete_image(path: str) -> None:
    """Delete the file at *path* from default_storage if it exists."""
    if default_storage.exists(path):
        default_storage.delete(path)


def get_image_etag(path: str) -> str | None:
    """Return an HMAC-SHA256 ETag based on the file's modification time, or *None*."""
    try:
        mtime = default_storage.get_modified_time(path).timestamp()
    except (FileNotFoundError, OSError):
        return None
    raw = f"{path}-{mtime}"
    return hmac.new(_ETAG_SECRET, raw.encode(), "sha256").hexdigest()
