from io import BytesIO

from django.core.files.storage import default_storage
from PIL import Image

from workspace.common.services.image import (
    delete_image,
    get_image_etag,
    process_image_to_webp,
    save_image,
)


def avatar_path(person):
    return f"people/avatars/{person.uuid}.webp"


def save_avatar(person, image_file, crop_x, crop_y, crop_w, crop_h):
    image_bytes = process_image_to_webp(image_file, crop_x, crop_y, crop_w, crop_h)
    save_image(avatar_path(person), image_bytes)
    person.has_avatar = True
    # The row is written even on a re-upload: the blob keeps its path, so
    # `updated_at` is the only thing that can bust a cached avatar.
    person.save(update_fields=["has_avatar", "updated_at"])


def delete_avatar(person):
    delete_image(avatar_path(person))
    if person.has_avatar:
        person.has_avatar = False
        person.save(update_fields=["has_avatar", "updated_at"])


def avatar_etag(person):
    return get_image_etag(avatar_path(person))


def save_avatar_from_card(person, photo):
    """Make the image bytes of a vCard ``PHOTO`` the person's avatar.

    Cropped to its centred square and stored at avatar size, whatever the
    card carried. Returns False, leaving the avatar alone, for bytes that
    are not an image Pillow will open.
    """
    try:
        with Image.open(BytesIO(photo)) as image:
            width, height = image.size
    except OSError, ValueError, Image.DecompressionBombError:
        return False
    side = min(width, height)
    try:
        save_avatar(
            person, BytesIO(photo), (width - side) / 2, (height - side) / 2, side, side
        )
    except OSError, ValueError, Image.DecompressionBombError:
        return False
    return True


def avatar_bytes(person):
    """The stored WebP avatar, or None."""
    if not person.has_avatar:
        return None
    path = avatar_path(person)
    if not default_storage.exists(path):
        return None
    with default_storage.open(path, "rb") as handle:
        return handle.read()


def avatar_jpeg(person):
    """The avatar as JPEG, the one image type every contacts app decodes."""
    webp = avatar_bytes(person)
    if webp is None:
        return None
    try:
        with Image.open(BytesIO(webp)) as image:
            buf = BytesIO()
            image.convert("RGB").save(buf, format="JPEG", quality=85)
    except OSError, ValueError:
        return None
    return buf.getvalue()
