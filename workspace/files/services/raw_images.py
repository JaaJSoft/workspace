"""Camera RAW photos, read through the JPEG preview the camera embeds.

Decoding the sensor data takes a demosaicing library and seconds per photo.
Every RAW format also carries a JPEG the camera rendered itself, usually at
full size, which is what a thumbnail, a face or the viewer wants. Its EXIF is
often missing or stripped, so the date, camera, orientation and GPS position
are read from the RAW's own header instead.

The browser does the same extraction for the viewer
(``files/ui/js/raw_preview.js``); the two must agree.
"""

import io
import struct

from PIL import ExifTags, Image, JpegImagePlugin

# Label -> MIME type and extensions, the shared-mime-info names.
RAW_FORMATS = {
    "dng": ("image/x-adobe-dng", ["dng"]),
    "cr2": ("image/x-canon-cr2", ["cr2"]),
    "cr3": ("image/x-canon-cr3", ["cr3"]),
    "nef": ("image/x-nikon-nef", ["nef", "nrw"]),
    "arw": ("image/x-sony-arw", ["arw", "srf", "sr2"]),
    "orf": ("image/x-olympus-orf", ["orf"]),
    "rw2": ("image/x-panasonic-rw2", ["rw2"]),
    "pef": ("image/x-pentax-pef", ["pef"]),
    "raf": ("image/x-fuji-raf", ["raf"]),
}
RAW_LABELS = frozenset(RAW_FORMATS)

# Enough to hold IFD0 and the strings it points to in every RAW seen so far.
SNIFF_SIZE = 64 * 1024
# The whole file is read to find its preview: past this, it is not opened.
# Medium-format backs write about 200 MB.
MAX_RAW_SIZE = 512 * 1024 * 1024
# The bytes the preview search may walk, as a multiple of the file size. A
# real RAW walks each of its JPEGs once and turns down a false start within
# a few bytes, about 1x.
SCAN_BUDGET_FACTOR = 4

_TIFF_MAGICS = (b"II*\x00", b"MM\x00*")
# Olympus and Panasonic write a TIFF under a magic number of their own.
_VENDOR_TIFF_MAGICS = {
    b"IIRO": "orf",
    b"IIRS": "orf",
    b"MMOR": "orf",
    b"IIU\x00": "rw2",
}
_TAG_COMPRESSION = 0x0103
_TAG_PHOTOMETRIC = 0x0106
_TAG_MAKE = 0x010F
_TAG_SUB_IFDS = 0x014A
_TAG_DNG_VERSION = 0xC612
_PHOTOMETRIC_CFA = 32803
_COMPRESSION_VENDOR = 65535
# A plain TIFF a scanner of the same brand writes (a Nikon Coolscan) carries
# the make too, but stores its picture in IFD0: no sub-IFD, no sensor mosaic.
_TIFF_RAW_MAKES = (
    ("NIKON", "nef"),
    ("SONY", "arw"),
    ("PENTAX", "pef"),
    ("RICOH", "pef"),
)


def _ifd0(head):
    """IFD0 of the TIFF *head* opens on, as {tag: (type, count, value bytes)}."""
    endian = "<" if head[:2] == b"II" else ">"
    (offset,) = struct.unpack_from(endian + "I", head, 4)
    (count,) = struct.unpack_from(endian + "H", head, offset)
    entries = {}
    for i in range(count):
        tag, typ, n, value = struct.unpack_from(
            endian + "HHI4s", head, offset + 2 + 12 * i
        )
        entries[tag] = (typ, n, value)
    return endian, entries


def _short(endian, entry):
    if entry is None or entry[0] != 3:
        return None
    return struct.unpack_from(endian + "H", entry[2])[0]


def _ascii(head, endian, entry):
    if entry is None or entry[0] != 2:
        return ""
    _, n, value = entry
    raw = value[:n] if n <= 4 else head[struct.unpack(endian + "I", value)[0] :][:n]
    return raw.split(b"\x00", 1)[0].decode("latin-1").strip()


def raw_label(head):
    """The RAW label *head* (the first SNIFF_SIZE bytes) opens on, or None."""
    if head.startswith(b"FUJIFILMCCD-RAW"):
        return "raf"
    if head[4:12] == b"ftypcrx ":
        return "cr3"
    if head[:4] in _VENDOR_TIFF_MAGICS:
        return _VENDOR_TIFF_MAGICS[head[:4]]
    if head[:4] not in _TIFF_MAGICS:
        return None
    if head[8:10] == b"CR":
        return "cr2"
    try:
        endian, tags = _ifd0(head)
    except struct.error:
        return None
    if _TAG_DNG_VERSION in tags:
        return "dng"
    is_sensor_data = (
        _TAG_SUB_IFDS in tags
        or _short(endian, tags.get(_TAG_PHOTOMETRIC)) == _PHOTOMETRIC_CFA
        or _short(endian, tags.get(_TAG_COMPRESSION)) == _COMPRESSION_VENDOR
    )
    if not is_sensor_data:
        return None
    make = _ascii(head, endian, tags.get(_TAG_MAKE)).upper()
    return next(
        (label for prefix, label in _TIFF_RAW_MAKES if make.startswith(prefix)), None
    )


_SOI = b"\xff\xd8\xff"
# Start-of-frame markers, which carry the picture's size: every SOFn but
# DHT (C4), JPG (C8) and DAC (CC), which share the range.
_FRAME_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


def _jpeg_at(data, start):
    """``(end, pixels, reached)`` of the JPEG starting at *start*: *end* is
    None when it is not one, *reached* how far the walk read either way. The
    size comes from the frame header, nothing is decoded.

    Walks the marker segments rather than looking for the next FFD9: the
    preview's own EXIF thumbnail is a JPEG nested inside it.
    """
    pos = start + 2
    size = len(data)
    pixels = 0
    while pos + 4 <= size:
        if data[pos] != 0xFF:
            return None, 0, pos
        marker = data[pos + 1]
        if marker == 0xFF:
            pos += 1
            continue
        (length,) = struct.unpack_from(">H", data, pos + 2)
        if marker in _FRAME_MARKERS and pos + 9 <= size:
            height, width = struct.unpack_from(">HH", data, pos + 5)
            pixels = width * height
        pos += 2 + length
        if marker != 0xDA:
            continue
        # Entropy-coded data: FF00 is a stuffed byte and FFD0-FFD7 a restart
        # marker, any other FFxx ends the scan (a progressive JPEG has several).
        while (pos := data.find(b"\xff", pos)) != -1 and pos + 1 < size:
            follower = data[pos + 1]
            if follower == 0x00 or 0xD0 <= follower <= 0xD7:
                pos += 2
            elif follower == 0xFF:
                pos += 1
            elif follower == 0xD9:
                return pos + 2, pixels, pos + 2
            else:
                break
        else:
            return None, 0, size
    return None, 0, size


def largest_jpeg(data):
    """The largest JPEG embedded in *data*, by pixel count, or None.

    Every start-of-image byte pair is a candidate, and a forged one can walk
    to the end of the file before failing: the bytes walked are capped at a
    few times the file size, so a file full of them costs a linear scan
    rather than a quadratic one.
    """
    budget = SCAN_BUDGET_FACTOR * len(data)
    best, best_pixels = None, 0
    pos = data.find(_SOI)
    while pos != -1 and budget > 0:
        end, pixels, reached = _jpeg_at(data, pos)
        budget -= reached - pos
        if end is None:
            pos = data.find(_SOI, pos + 3)
            continue
        if pixels > best_pixels:
            best, best_pixels = data[pos:end], pixels
        pos = data.find(_SOI, end)
    return best


def _cr3_ifd(data, box):
    """The first directory of the TIFF a CR3 stores in its CMT1-CMT4 *box*."""
    pos = data.find(box, 0, SNIFF_SIZE)
    if pos < 4:
        return {}
    (size,) = struct.unpack_from(">I", data, pos - 4)
    exif = Image.Exif()
    exif.load(data[pos + 4 : pos - 4 + size])
    return dict(exif)


# What IFD0 says about the photo. The rest of a RAW's IFD0 describes the file
# (strip offsets, sub-IFDs, colour matrices) and runs past the 64 KB a JPEG
# EXIF segment holds, which an edited download writes.
_PHOTO_TAGS = (
    ExifTags.Base.ImageDescription,
    ExifTags.Base.Make,
    ExifTags.Base.Model,
    ExifTags.Base.Orientation,
    ExifTags.Base.Software,
    ExifTags.Base.DateTime,
    ExifTags.Base.Artist,
    ExifTags.Base.Copyright,
)


def raw_exif(data, label):
    """The EXIF of the photo in the RAW *data*, or None when its preview's is
    the one to read (a RAF keeps the full EXIF in its preview).

    A TIFF-based RAW is EXIF already; a CR3 splits it over the CMT boxes of
    its header. Vendor maker notes are left out, for the same reason as the
    file-describing tags of IFD0.
    """
    if label == "raf":
        return None
    if label == "cr3":
        ifd0 = _cr3_ifd(data, b"CMT1")
        exif_ifd = _cr3_ifd(data, b"CMT2")
        gps_ifd = _cr3_ifd(data, b"CMT4")
    else:
        if data[:4] in _VENDOR_TIFF_MAGICS:
            data = (b"II*\x00" if data[:1] == b"I" else b"MM\x00*") + data[4:]
        raw = Image.Exif()
        raw.load(data)
        ifd0 = dict(raw)
        exif_ifd = raw.get_ifd(ExifTags.IFD.Exif)
        gps_ifd = raw.get_ifd(ExifTags.IFD.GPSInfo)
    exif = Image.Exif()
    for tag in _PHOTO_TAGS:
        if tag in ifd0:
            exif[tag] = ifd0[tag]
    # Built rather than loaded, so there is no offset for Pillow to read a
    # sub-IFD from: get_ifd and tobytes both go by this dict instead.
    exif._ifds[ExifTags.IFD.Exif] = {
        tag: value for tag, value in exif_ifd.items() if tag != ExifTags.Base.MakerNote
    }
    if gps_ifd:
        exif._ifds[ExifTags.IFD.GPSInfo] = dict(gps_ifd)
    return exif


class RawImageFile(JpegImagePlugin.JpegImageFile):
    """A camera RAW opened as its embedded preview, with the RAW's EXIF.

    Being the preview JPEG to Pillow is what keeps ``draft()``: a thumbnail
    decodes a 24 MP preview at an eighth of its size.
    """

    format = "RAW"
    format_description = "Camera RAW (embedded preview)"

    def _open(self):
        head = self.fp.read(SNIFF_SIZE)
        label = raw_label(head)
        if label is None:
            msg = "not a camera RAW"
            raise SyntaxError(msg)
        data = head + self.fp.read(MAX_RAW_SIZE + 1 - len(head))
        if len(data) > MAX_RAW_SIZE:
            msg = "camera RAW too large to open"
            raise SyntaxError(msg)
        preview = largest_jpeg(data)
        if preview is None:
            msg = "camera RAW without an embedded preview"
            raise SyntaxError(msg)
        try:
            self._raw_exif = raw_exif(data, label)
        except OSError, SyntaxError, ValueError, struct.error:
            self._raw_exif = None
        # The decoder reads the copy; the RAW's stream is kept for close().
        self._fp = self.fp
        self.fp = io.BytesIO(preview)
        super()._open()

    def load_end(self):
        super().load_end()
        # load() closes only the preview's copy of an Image.open(path).
        if self._exclusive_fp:
            self._fp.close()

    def getexif(self):
        if self._raw_exif is None:
            return super().getexif()
        return self._raw_exif


def _accept(prefix):
    return (
        prefix[:4] in _TIFF_MAGICS
        or prefix[:4] in _VENDOR_TIFF_MAGICS
        or prefix.startswith(b"FUJIFILMCCD-RAW")
        or prefix[4:12] == b"ftypcrx "
    )


def register_raw_opener():
    """Teach Pillow the RAW formats, ahead of its TIFF plugin.

    Most RAW formats are TIFFs that Pillow's own plugin would open as the
    160x120 thumbnail of their first directory. ``_open`` turns down a plain
    TIFF, and Pillow moves on to the next format.
    """
    Image.register_open(RawImageFile.format, RawImageFile, _accept)
    Image.ID.remove(RawImageFile.format)
    Image.ID.insert(0, RawImageFile.format)
