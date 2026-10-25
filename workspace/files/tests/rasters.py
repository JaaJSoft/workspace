"""Still-image fixtures in the formats Pillow only reads through a plugin, or
that share their container with video, shared by the files and photos tests."""

import io

from PIL import ExifTags, Image


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


def jpeg_bytes(*, size=(64, 48), color=(200, 60, 40), exif=None):
    buf = io.BytesIO()
    params = {"exif": exif.tobytes()} if exif is not None else {}
    Image.new("RGB", size, color).save(buf, format="JPEG", **params)
    return buf.getvalue()


def _raw_ifd0(make, model, orientation, taken_at, extra):
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = make
    exif[ExifTags.Base.Model] = model
    exif[ExifTags.Base.Orientation] = orientation
    exif[ExifTags.IFD.Exif] = {ExifTags.Base.DateTimeOriginal: taken_at}
    for tag, value in extra.items():
        exif[tag] = value
    return exif


def tiff_raw_bytes(
    *,
    make="NIKON CORPORATION",
    model="NIKON D3200",
    orientation=1,
    taken_at="2016:11:30 13:10:53",
    preview_size=(96, 64),
    sub_ifds=True,
    dng=False,
    magic=None,
    extra=None,
):
    """A TIFF-based camera RAW the way a NEF is laid out: IFD0 names the
    camera and points at sub-IFDs, and the file carries a small thumbnail and
    a larger JPEG preview. No sensor data: nothing reads it.

    *magic* replaces the TIFF magic number (b"IIRO" for an ORF); *extra* adds
    IFD0 tags.
    """
    tags = dict(extra or {})
    if sub_ifds:
        tags[ExifTags.Base.SubIFDs] = 8
    if dng:
        tags[ExifTags.Base.DNGVersion] = b"\x01\x04\x00\x00"
    ifd0 = _raw_ifd0(make, model, orientation, taken_at, tags)
    # The byte order follows the magic, as in the vendor TIFFs it stands for.
    ifd0.endian = "<" if (magic or b"II")[:2] == b"II" else ">"
    tiff = ifd0.tobytes()[6:]
    if magic is not None:
        tiff = magic + tiff[4:]
    thumbnail = jpeg_bytes(size=(16, 12), color=(0, 0, 255))
    return tiff + thumbnail + jpeg_bytes(size=preview_size)


def _box(kind, payload):
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


def cr3_bytes(
    *,
    make="Canon",
    model="Canon EOS R6",
    taken_at="2021:05:12 12:21:21",
    orientation=1,
    preview_size=(96, 64),
):
    """A CR3 header: the crx brand, the CMT boxes holding IFD0 and the Exif
    IFD as TIFFs of their own, then the preview."""
    ifd0 = Image.Exif()
    ifd0[ExifTags.Base.Make] = make
    ifd0[ExifTags.Base.Model] = model
    ifd0[ExifTags.Base.Orientation] = orientation
    exif_ifd = Image.Exif()
    exif_ifd[ExifTags.Base.DateTimeOriginal] = taken_at
    return (
        ftyp_box(b"crx ", b"crx ", b"isom")
        + _box(b"CMT1", ifd0.tobytes()[6:])
        + _box(b"CMT2", exif_ifd.tobytes()[6:])
        + jpeg_bytes(size=preview_size)
    )


def raf_bytes(*, preview_size=(96, 64), exif=None):
    """A RAF: its own header, then a preview JPEG carrying the full EXIF."""
    header = b"FUJIFILMCCD-RAW 0201FF189504X-T50".ljust(148, b"\x00")
    return header + jpeg_bytes(size=preview_size, exif=exif)
