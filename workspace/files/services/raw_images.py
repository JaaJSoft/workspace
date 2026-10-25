"""Camera RAW photos, read with LibRaw (rawpy) through the preview they embed.

Every RAW format carries a JPEG the camera rendered itself, usually at full
size: what a thumbnail, a face or the viewer wants, without demosaicing the
sensor data. LibRaw hands it out with an EXIF block of its own (camera, date,
orientation, exposure, GPS) read from whichever header the format uses.

Content detection knows no RAW: Magika reads most of them as TIFF, the
container they share. A file is a RAW when its extension names one and LibRaw
opens it.
"""

import io

from PIL import ExifTags, Image, JpegImagePlugin, TiffImagePlugin

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
RAW_EXTENSIONS = tuple(
    extension for _, extensions in RAW_FORMATS.values() for extension in extensions
)
# LibRaw reads a RAW from memory: past this, it is not read at all. Medium
# format backs write about 200 MB.
MAX_RAW_SIZE = 512 * 1024 * 1024
_LABEL_BY_EXTENSION = {
    extension: label
    for label, (_, extensions) in RAW_FORMATS.items()
    for extension in extensions
}


def label_for_name(filename):
    """The RAW label *filename*'s extension names, or None."""
    _, dot, extension = (filename or "").rpartition(".")
    return _LABEL_BY_EXTENSION.get(extension.lower()) if dot else None


def _rawpy():
    # Imported on first use: it pulls in numpy, which the detection helpers
    # importing this module would otherwise carry into every process.
    import rawpy

    return rawpy


def _read_capped(stream):
    """The bytes of *stream*, or None past MAX_RAW_SIZE."""
    data = stream.read(MAX_RAW_SIZE + 1)
    return None if len(data) > MAX_RAW_SIZE else data


def is_camera_raw(stream):
    """Whether LibRaw opens the file in *stream*; its position is kept."""
    rawpy = _rawpy()
    position = stream.tell()
    try:
        data = _read_capped(stream)
        if data is None:
            return False
        with rawpy.imread(io.BytesIO(data)):
            return True
    except rawpy.LibRawError, OSError:
        return False
    finally:
        stream.seek(position)


def _preview_jpeg(data):
    """The RAW's embedded preview as JPEG bytes, with LibRaw's EXIF block."""
    rawpy = _rawpy()
    with rawpy.imread(io.BytesIO(data)) as raw:
        thumb = raw.extract_thumb()
    if thumb.format == rawpy.ThumbFormat.JPEG:
        return thumb.data
    # A few older bodies embed an uncompressed bitmap instead. Pillow checks
    # the pixel count of what it opens, but only once it is built.
    height, width = thumb.data.shape[:2]
    if Image.MAX_IMAGE_PIXELS and width * height > Image.MAX_IMAGE_PIXELS:
        msg = "camera RAW preview too large"
        raise Image.DecompressionBombError(msg)
    buffer = io.BytesIO()
    Image.fromarray(thumb.data).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


class RawImageFile(JpegImagePlugin.JpegImageFile):
    """A camera RAW opened as its embedded preview.

    Being the preview JPEG to Pillow is what keeps ``draft()``: a thumbnail
    decodes a 24 MP preview at an eighth of its size.
    """

    format = "RAW"
    format_description = "Camera RAW (embedded preview)"

    def _open(self):
        data = _read_capped(self.fp)
        if data is None:
            msg = "camera RAW too large to open"
            raise SyntaxError(msg)
        try:
            preview = _preview_jpeg(data)
        except (_rawpy().LibRawError, OSError) as exc:
            raise SyntaxError(str(exc)) from exc
        # The decoder reads the preview; the RAW's stream is kept for close().
        self._fp = self.fp
        self.fp = io.BytesIO(preview)
        super()._open()

    def load_end(self):
        super().load_end()
        # load() closes only the preview's stream of an Image.open(path).
        if self._exclusive_fp:
            self._fp.close()

    def getexif(self):
        exif = super().getexif()
        # LibRaw writes the capture time as DateTime, where a camera JPEG
        # keeps it in DateTimeOriginal.
        captured = exif.get(ExifTags.Base.DateTime)
        exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
        if captured and ExifTags.Base.DateTimeOriginal not in exif_ifd:
            exif_ifd[ExifTags.Base.DateTimeOriginal] = captured
        return exif


def _is_tiff(prefix):
    return prefix[:4] in TiffImagePlugin.PREFIXES


def register_raw_opener():
    """Teach Pillow the RAW formats.

    Twice: ahead of the TIFF plugin, which would open a NEF or a DNG as the
    160x120 thumbnail of its first directory (LibRaw turns a plain TIFF
    down, and Pillow moves on), and after every other plugin, for the RAWs no
    plugin claims (CR3, RAF, ORF, RW2).
    """
    Image.init()
    Image.register_open("RAW", RawImageFile, _is_tiff)
    Image.ID.remove("RAW")
    Image.ID.insert(0, "RAW")
    Image.register_open("RAW_OTHER", RawImageFile)
