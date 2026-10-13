"""Decode a library photo for a model: as displayed, scaled down, in RGB."""

import logging

from PIL import Image, ImageOps, UnidentifiedImageError

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)


def decode_photo(file_obj, size, *, reader):
    """The photo as displayed, at most *size* on a side, as a PIL RGB image.

    None when the bytes are not a picture Pillow can read, False when the
    blob could not be opened at all. *reader* names the caller in the logs.
    """
    try:
        handle = file_obj.content.open("rb")
    except OSError as exc:
        logger.warning(
            "%s cannot read the blob of %s: %s",
            reader,
            scrub(file_obj.content.name),
            scrub(str(exc)),
        )
        return False
    try:
        with Image.open(handle) as img:
            # A JPEG decodes straight at a fraction of its size.
            img.draft("RGB", (size, size))
            img = ImageOps.exif_transpose(img).convert("RGB")
            img.thumbnail((size, size), Image.LANCZOS)
            return img
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
    ) as exc:
        logger.info(
            "%s could not decode %s: %s",
            reader,
            scrub(file_obj.content.name),
            scrub(str(exc)),
        )
        return None
    finally:
        handle.close()
