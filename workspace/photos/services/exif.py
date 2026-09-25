"""Read the capture metadata of a raster image: date, dimensions, camera.

Only the header is parsed. Pillow opens images lazily, and the size and EXIF
block are both known before any pixel is decoded, so a 40 MB photo costs a
few kilobytes of reading here.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import timezone as dt_timezone

from django.utils import timezone
from PIL import ExifTags, Image

# The orientations under which the stored pixels are rotated a quarter turn
# (ImageOps.exif_transpose): the displayed width is the stored height.
_QUARTER_TURN_ORIENTATIONS = frozenset({5, 6, 7, 8})

_DATETIME_FORMATS = ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")
_OFFSET_RE = re.compile(r"^(?P<sign>[+-])(?P<hours>\d{2}):(?P<minutes>\d{2})$")
_MAX_OFFSET = timedelta(hours=14)

CAMERA_FIELD_LENGTH = 128


@dataclass(frozen=True)
class PhotoMetadata:
    taken_at: datetime | None = None
    width: int | None = None
    height: int | None = None
    camera_make: str = ""
    camera_model: str = ""


def read_metadata(stream, *, default_tz=UTC):
    """Return the :class:`PhotoMetadata` of the image in *stream*.

    *default_tz* reads a capture time that carries no offset: EXIF stores the
    camera's wall clock, and without OffsetTimeOriginal the owner's timezone
    is the best guess at where that clock was.

    Raises whatever Pillow raises for bytes it cannot identify.
    """
    with Image.open(stream) as img:
        width, height = img.size
        exif = img.getexif()
        exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)

    if exif.get(ExifTags.Base.Orientation) in _QUARTER_TURN_ORIENTATIONS:
        width, height = height, width

    return PhotoMetadata(
        taken_at=_capture_time(
            exif_ifd.get(ExifTags.Base.DateTimeOriginal),
            exif_ifd.get(ExifTags.Base.OffsetTimeOriginal),
            default_tz,
        ),
        width=width,
        height=height,
        camera_make=_text(exif.get(ExifTags.Base.Make)),
        camera_model=_text(exif.get(ExifTags.Base.Model)),
    )


def _text(value):
    """An EXIF ASCII value as a clean string; cameras pad with NULs."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if not isinstance(value, str):
        return ""
    return value.replace("\x00", "").strip()[:CAMERA_FIELD_LENGTH]


def _capture_time(raw_datetime, raw_offset, default_tz):
    raw_datetime = _text(raw_datetime)
    if not raw_datetime:
        return None
    # Cameras without a clock write placeholders ("0000:00:00 00:00:00",
    # blanks), which fail to parse and read as "no capture date".
    naive = None
    for fmt in _DATETIME_FORMATS:
        try:
            naive = datetime.strptime(raw_datetime[:19], fmt)
            break
        except ValueError:
            continue
    if naive is None:
        return None

    offset = _utc_offset(_text(raw_offset))
    if offset is not None:
        return naive.replace(tzinfo=offset)
    return timezone.make_aware(naive, default_tz)


def _utc_offset(raw):
    match = _OFFSET_RE.match(raw)
    if match is None:
        return None
    delta = timedelta(hours=int(match["hours"]), minutes=int(match["minutes"]))
    if delta > _MAX_OFFSET or int(match["minutes"]) >= 60:
        return None
    return dt_timezone(-delta if match["sign"] == "-" else delta)
