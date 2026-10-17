"""Rotation and crop of an image file, kept beside its bytes rather than in them.

``File.image_edit`` holds what to show in place of the raw image, and the blob
is never rewritten: reverting is forgetting the edit. The value is

    {"rotation": 90, "crop": {"x": 0.1, "y": 0.0, "width": 0.8, "height": 1.0}}

``rotation`` turns the image clockwise, in quarter turns, after its EXIF
orientation is applied. ``crop`` is a rectangle in fractions of the *rotated*
image, absent when the whole of it is kept. An edit that changes nothing is
stored as ``None``, so "edited" and "has a value" are the same thing.

The browser applies the same geometry for the viewer
(``files/ui/js/image_edit.js``); the two must agree.
"""

import hashlib
import logging

from workspace.common.logging import scrub

from .raster_formats import RASTER_LABELS

logger = logging.getLogger(__name__)

ROTATIONS = (0, 90, 180, 270)
# The narrowest crop side, as a fraction of the image: below it a handle drag
# would collapse the rectangle to nothing.
MIN_CROP = 0.02
# Fractions are stored rounded, so that the same crop always hashes to the
# same thumbnail version.
_PRECISION = 4
_EPSILON = 1e-6
_RECT_KEYS = ("x", "y", "width", "height")
_FULL = {"x": 0, "y": 0, "width": 1, "height": 1}


def can_edit_type(file_type):
    """Whether a file of content label *file_type* can be rotated and cropped."""
    return file_type in RASTER_LABELS


def normalize_edit(rotation=0, crop=None):
    """The stored form of a rotation and a crop, ``None`` when it changes nothing.

    Raises ``ValueError`` on a rotation that is not a quarter turn or a crop
    that does not fit in the image.
    """
    if rotation not in ROTATIONS:
        raise ValueError("Rotation must be 0, 90, 180 or 270.")
    edit = {"rotation": rotation} if rotation else {}
    if crop is not None:
        rect = {key: round(float(crop[key]), _PRECISION) for key in _RECT_KEYS}
        if rect["width"] < MIN_CROP or rect["height"] < MIN_CROP:
            raise ValueError("The crop is too small.")
        if (
            rect["x"] < 0
            or rect["y"] < 0
            or rect["x"] + rect["width"] > 1 + _EPSILON
            or rect["y"] + rect["height"] > 1 + _EPSILON
        ):
            raise ValueError("The crop must fit in the image.")
        if rect != _FULL:
            edit["crop"] = rect
    return edit or None


def rotate_edit(edit, quarter_turns):
    """*edit* turned by *quarter_turns* more (negative: anticlockwise).

    The crop turns with the image, so it frames the same part of it.
    """
    edit = edit or {}
    rotation = edit.get("rotation", 0)
    crop = dict(edit.get("crop") or _FULL)
    for _ in range(quarter_turns % 4):
        crop = {
            "x": 1 - crop["y"] - crop["height"],
            "y": crop["x"],
            "width": crop["height"],
            "height": crop["width"],
        }
    return normalize_edit((rotation + 90 * quarter_turns) % 360, crop)


def edited_size(width, height, edit):
    """The size an image of *width* x *height* (orientation applied) shows at."""
    if not edit:
        return width, height
    if edit.get("rotation", 0) in (90, 270):
        width, height = height, width
    crop = edit.get("crop")
    if crop:
        width = max(1, round(width * crop["width"]))
        height = max(1, round(height * crop["height"]))
    return width, height


def crop_scale(edit):
    """How much of the image's short side the edit keeps at most, in (0, 1].

    A thumbnail decoded at reduced scale has to be decoded that much larger
    for the crop to still fill it.
    """
    crop = (edit or {}).get("crop")
    if not crop:
        return 1.0
    return min(crop["width"], crop["height"])


def apply_edit(img, edit):
    """The Pillow image *img* (orientation applied) as *edit* shows it."""
    from PIL import Image

    if not edit:
        return img
    rotation = edit.get("rotation", 0)
    if rotation:
        # Pillow's ROTATE_* constants turn anticlockwise.
        img = img.transpose(
            {
                90: Image.Transpose.ROTATE_270,
                180: Image.Transpose.ROTATE_180,
                270: Image.Transpose.ROTATE_90,
            }[rotation]
        )
    crop = edit.get("crop")
    if crop:
        width, height = img.size
        left = round(crop["x"] * width)
        top = round(crop["y"] * height)
        right = max(left + 1, round((crop["x"] + crop["width"]) * width))
        bottom = max(top + 1, round((crop["y"] + crop["height"]) * height))
        img = img.crop((left, top, min(right, width), min(bottom, height)))
    return img


def edit_version(edit):
    """A short token naming *edit*, empty for an unedited image.

    Thumbnails are cached for a day under their URL, so the URL of an edited
    image's thumbnail carries this: a new edit is a new URL.
    """
    if not edit:
        return ""
    # Built from the numbers, not the JSON: 1 and 1.0 are the same crop.
    crop = edit.get("crop") or _FULL
    payload = ",".join(
        f"{round(float(value), _PRECISION):g}"
        for value in (edit.get("rotation", 0), *(crop[key] for key in _RECT_KEYS))
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def versioned_thumbnail_url(url, file_obj):
    """*url*, a thumbnail of *file_obj*, made unique to the edit it shows."""
    version = edit_version(getattr(file_obj, "image_edit", None))
    if not url or not version:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}v={version}"


def save_image_edit(file_obj, edit, *, acting_user):
    """Store *edit* (the output of normalize_edit) on *file_obj*, or refuse.

    The write is conditional, like a content write: it refuses a row someone
    else holds the lock on (LockConflict), and a row whose bytes changed since
    *file_obj* was read (StaleContent) - the crop was drawn on those bytes.
    The thumbnail is redrawn before returning: the caller is waiting to see it.
    ``updated_at`` stays: it dates the bytes, which the content endpoint's
    ETag (and so every cached copy of the original) is built on.
    """
    from workspace.files.models import File

    from .locking import LockConflict, StaleContent, conflicting_lock, unlocked_for_q
    from .thumbnails.generation import refresh_thumbnail

    claimed = (
        File.objects.filter(
            pk=file_obj.pk,
            deleted_at__isnull=True,
            content_hash=file_obj.content_hash,
        )
        .filter(unlocked_for_q(acting_user))
        .update(image_edit=edit)
    )
    if not claimed:
        current = (
            File.objects.select_related("locked_by").filter(pk=file_obj.pk).first()
        )
        holder = conflicting_lock(current, acting_user)
        if holder is not None:
            raise LockConflict(holder)
        raise StaleContent(current.content_hash if current else "")
    file_obj.image_edit = edit

    try:
        refresh_thumbnail(file_obj)
    except Exception:
        # The edit stands without a fresh thumbnail; the tile keeps the old one.
        logger.warning(
            "Could not redraw the thumbnail of %s", scrub(file_obj.uuid), exc_info=True
        )
    return file_obj
