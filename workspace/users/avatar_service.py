"""Avatar processing and storage service.

Avatars are stored at a deterministic path ``avatars/{user_id}.webp`` using
Django's ``default_storage``.  No database field is needed — the presence of
an avatar is tracked via a ``UserSetting(module='profile', key='has_avatar')``.
"""

from __future__ import annotations

import logging

from workspace.common.image_service import (
    delete_image,
    get_image_etag,
    process_image_to_webp,
    save_image,
)
from workspace.users.settings_service import delete_setting, get_setting, set_setting

logger = logging.getLogger(__name__)


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
    image_bytes = process_image_to_webp(image_file, crop_x, crop_y, crop_w, crop_h)
    path = get_avatar_path(user.id)
    save_image(path, image_bytes)
    set_setting(user, "profile", "has_avatar", True)
    logger.info("Avatar saved for user %s", user.id)


def delete_avatar(user) -> None:
    """Delete the user's avatar file and clear the setting flag."""
    path = get_avatar_path(user.id)
    delete_image(path)
    delete_setting(user, "profile", "has_avatar")
    logger.info("Avatar deleted for user %s", user.id)


def get_avatar_etag(user_id: int) -> str | None:
    """Return an ETag string based on the file's modification time, or *None*."""
    path = get_avatar_path(user_id)
    return get_image_etag(path)
