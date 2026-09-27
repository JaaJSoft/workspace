"""Still-image fixtures in the formats Pillow only reads through a plugin, or
that share their container with video, shared by the files and photos tests."""

import io

from PIL import Image


def heic_bytes(*, size=(64, 48), color=(200, 60, 40), exif=None):
    """A HEIC the way a phone writes one: ftyp major brand ``heic``.

    Magika files these small ones under mp4, like a real iPhone photo, which
    is what makes them worth testing. *exif* is an ``Image.Exif``.
    """
    buf = io.BytesIO()
    params = {"exif": exif.tobytes()} if exif is not None else {}
    Image.new("RGB", size, color).save(buf, format="HEIF", **params)
    return buf.getvalue()


def avif_bytes(*, size=(64, 48), color=(200, 60, 40)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="AVIF")
    return buf.getvalue()


def ftyp_box(major, *compatible):
    """The ftyp box an ISO base media file opens on, for brand-only tests."""
    body = major + b"\x00\x00\x00\x00" + b"".join(compatible)
    return (8 + len(body)).to_bytes(4, "big") + b"ftyp" + body
