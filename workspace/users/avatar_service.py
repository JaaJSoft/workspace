"""Avatar processing and storage service.

Avatars are stored at a deterministic path ``avatars/{user_id}.webp`` using
Django's ``default_storage``.  No database field is needed — the presence of
an avatar is tracked via a ``UserSetting(module='profile', key='has_avatar')``.
"""

from __future__ import annotations

import hmac
import os
import logging
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image, ImageOps

from workspace.users.settings_service import delete_setting, get_setting, set_setting

_ETAG_SECRET = os.urandom(32)

logger = logging.getLogger(__name__)

AVATAR_SIZE = 256
WEBP_QUALITY = 85


def get_avatar_path(user_id: int) -> str:
    """Return the storage path for a user's avatar."""
    return f"avatars/{user_id}.webp"


def has_avatar(user) -> bool:
    """Check whether *user* has an uploaded avatar."""
    return get_setting(user, "profile", "has_avatar", default=False) is True


def process_and_save_avatar(
    user,
    image_file,
    crop_x: float,
    crop_y: float,
    crop_w: float,
    crop_h: float,
) -> None:
    """Process an uploaded image and save it as the user's avatar.

    Steps: EXIF transpose → crop → convert to RGB → resize 256×256 → save WebP.
    """
    img = Image.open(image_file)
    img = ImageOps.exif_transpose(img)

    # Crop
    left = int(crop_x)
    top = int(crop_y)
    right = int(crop_x + crop_w)
    bottom = int(crop_y + crop_h)
    img = img.crop((left, top, right, bottom))

    # Convert & resize
    img = img.convert("RGB")
    img = img.resize((AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)

    # Save to WebP in memory
    buf = BytesIO()
    img.save(buf, format="WEBP", quality=WEBP_QUALITY)
    buf.seek(0)

    path = get_avatar_path(user.id)
    # Remove old file first (default_storage.save won't overwrite)
    if default_storage.exists(path):
        default_storage.delete(path)
    default_storage.save(path, ContentFile(buf.read()))

    set_setting(user, "profile", "has_avatar", True)
    logger.info("Avatar saved for user %s", user.id)


def delete_avatar(user) -> None:
    """Delete the user's avatar file and clear the setting flag."""
    path = get_avatar_path(user.id)
    if default_storage.exists(path):
        default_storage.delete(path)
    delete_setting(user, "profile", "has_avatar")
    logger.info("Avatar deleted for user %s", user.id)


def get_avatar_etag(user_id: int) -> str | None:
    """Return an ETag string based on the file's modification time, or *None*."""
    path = get_avatar_path(user_id)
    try:
        mtime = default_storage.get_modified_time(path).timestamp()
    except (FileNotFoundError, OSError):
        return None
    raw = f"{user_id}-{mtime}"
    return hmac.new(_ETAG_SECRET, raw.encode(), "sha256").hexdigest()
