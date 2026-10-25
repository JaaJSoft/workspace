"""Still-image fixtures in the formats Pillow only reads through a plugin, or
that share their container with video, shared by the files and photos tests."""

import io
import struct

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


_BYTE, _ASCII, _SHORT, _LONG, _SRATIONAL = 1, 2, 3, 4, 10
_FORMATS = {_BYTE: "B", _SHORT: "H", _LONG: "I"}


def _ifd(entries, offset):
    """A little-endian TIFF directory at *offset*, its out-of-line values
    right after it. *entries* are ``(tag, type, value)``."""
    head_size = 2 + 12 * len(entries) + 4
    head = struct.pack("<H", len(entries))
    values = b""
    for tag, kind, value in sorted(entries):
        if kind == _ASCII:
            raw, count = value.encode() + b"\0", len(value) + 1
        elif kind == _SRATIONAL:
            raw, count = (
                b"".join(struct.pack("<ii", *pair) for pair in value),
                len(value),
            )
        else:
            raw, count = (
                struct.pack("<" + _FORMATS[kind] * len(value), *value),
                len(value),
            )
        if len(raw) <= 4:
            head += struct.pack("<HHI", tag, kind, count) + raw.ljust(4, b"\0")
        else:
            head += struct.pack(
                "<HHII", tag, kind, count, offset + head_size + len(values)
            )
            values += raw + b"\0" * (len(raw) % 2)
    return head + struct.pack("<I", 0) + values


def dng_bytes(
    *,
    make="Canon",
    model="EOS R6",
    orientation=1,
    taken_at="2021:05:12 12:21:21",
    preview_size=(96, 64),
    color=(200, 60, 40),
):
    """The smallest DNG LibRaw opens: a 64x64 blank sensor mosaic in a
    sub-IFD, and a JPEG preview of *preview_size* in IFD0 naming the camera.
    Real RAWs weigh megabytes; this one a few kilobytes."""
    buffer = io.BytesIO()
    Image.new("RGB", preview_size, color).save(buffer, format="JPEG")
    preview = buffer.getvalue() + b"\0" * (len(buffer.getvalue()) % 2)
    mosaic = bytes(64 * 64 * 2)

    out = bytearray(b"II*\0\0\0\0\0")
    preview_at = len(out)
    out += preview
    mosaic_at = len(out)
    out += mosaic
    sensor_at = len(out)
    out += _ifd(
        [
            (254, _LONG, [0]),  # NewSubfileType: the full-size image
            (256, _LONG, [64]),
            (257, _LONG, [64]),
            (258, _SHORT, [16]),
            (259, _SHORT, [1]),
            (262, _SHORT, [32803]),  # PhotometricInterpretation: CFA
            (273, _LONG, [mosaic_at]),
            (277, _SHORT, [1]),
            (278, _LONG, [64]),
            (279, _LONG, [len(mosaic)]),
            (33421, _SHORT, [2, 2]),  # CFARepeatPatternDim
            (33422, _BYTE, [0, 1, 1, 2]),  # CFAPattern: RGGB
        ],
        sensor_at,
    )
    exif_at = len(out)
    out += _ifd([(36867, _ASCII, taken_at)], exif_at)  # DateTimeOriginal
    ifd0_at = len(out)
    identity = [(1, 1), (0, 1), (0, 1), (0, 1), (1, 1), (0, 1), (0, 1), (0, 1), (1, 1)]
    out += _ifd(
        [
            (254, _LONG, [1]),  # NewSubfileType: a reduced-size preview
            (256, _LONG, [preview_size[0]]),
            (257, _LONG, [preview_size[1]]),
            (258, _SHORT, [8, 8, 8]),
            (259, _SHORT, [7]),  # Compression: JPEG
            (262, _SHORT, [6]),  # PhotometricInterpretation: YCbCr
            (271, _ASCII, make),
            (272, _ASCII, model),
            (273, _LONG, [preview_at]),
            (274, _SHORT, [orientation]),
            (277, _SHORT, [3]),
            (278, _LONG, [preview_size[1]]),
            (279, _LONG, [len(preview)]),
            (306, _ASCII, taken_at),
            (330, _LONG, [sensor_at]),  # SubIFDs
            (34665, _LONG, [exif_at]),  # ExifIFD
            (50706, _BYTE, [1, 4, 0, 0]),  # DNGVersion
            (50708, _ASCII, f"{make} {model}"),  # UniqueCameraModel
            (50721, _SRATIONAL, identity),  # ColorMatrix1
            (50778, _SHORT, [21]),  # CalibrationIlluminant1: D65
        ],
        ifd0_at,
    )
    struct.pack_into("<I", out, 4, ifd0_at)
    return bytes(out)
