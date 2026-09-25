"""Read the capture metadata of a raster image: date, dimensions, camera,
lens, exposure settings and GPS position.

Only the header is parsed. Pillow opens images lazily, and the size and EXIF
block are both known before any pixel is decoded, so a 40 MB photo costs a
few kilobytes of reading here.
"""

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import timezone as dt_timezone
from numbers import Rational

from django.utils import timezone
from PIL import ExifTags, Image

# The orientations under which the stored pixels are rotated a quarter turn
# (ImageOps.exif_transpose): the displayed width is the stored height.
_QUARTER_TURN_ORIENTATIONS = frozenset({5, 6, 7, 8})

_DATETIME_FORMATS = ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")
_OFFSET_RE = re.compile(r"^(?P<sign>[+-])(?P<hours>\d{2}):(?P<minutes>\d{2})$")
_MAX_OFFSET = timedelta(hours=14)

CAMERA_FIELD_LENGTH = 128

# Bounds past which a value is vendor garbage rather than a setting some
# camera could have used. They also keep every value inside its column.
_F_NUMBER_RANGE = (0.5, 1024)
_EXPOSURE_TIME_RANGE = (1e-6, 86_400)
_FOCAL_LENGTH_RANGE = (0.1, 10_000)
_ISO_RANGE = (1, 10_000_000)
_EXPOSURE_BIAS_RANGE = (-64, 64)
_ALTITUDE_RANGE = (-11_000, 100_000)

# EXIF Flash is a bit field; bit 0 says whether the flash fired, bit 5 that
# the camera has no flash at all.
_FLASH_FIRED = 0x01
_NO_FLASH_FUNCTION = 0x20


@dataclass(frozen=True)
class PhotoMetadata:
    taken_at: datetime | None = None
    width: int | None = None
    height: int | None = None
    camera_make: str = ""
    camera_model: str = ""
    lens_make: str = ""
    lens_model: str = ""
    focal_length: float | None = None
    focal_length_35mm: int | None = None
    f_number: float | None = None
    exposure_time: float | None = None
    iso: int | None = None
    exposure_bias: float | None = None
    flash_fired: bool | None = None
    latitude: float | None = None
    longitude: float | None = None
    altitude: float | None = None


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
        gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)

    if exif.get(ExifTags.Base.Orientation) in _QUARTER_TURN_ORIENTATIONS:
        width, height = height, width

    latitude, longitude, altitude = _position(gps_ifd)
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
        lens_make=_text(exif_ifd.get(ExifTags.Base.LensMake)),
        lens_model=_text(exif_ifd.get(ExifTags.Base.LensModel)),
        focal_length=_in_range(
            _number(exif_ifd.get(ExifTags.Base.FocalLength)), _FOCAL_LENGTH_RANGE
        ),
        focal_length_35mm=_integer(
            exif_ifd.get(ExifTags.Base.FocalLengthIn35mmFilm), _FOCAL_LENGTH_RANGE
        ),
        f_number=_in_range(
            _number(exif_ifd.get(ExifTags.Base.FNumber)), _F_NUMBER_RANGE
        ),
        exposure_time=_in_range(
            _number(exif_ifd.get(ExifTags.Base.ExposureTime)), _EXPOSURE_TIME_RANGE
        ),
        iso=_integer(exif_ifd.get(ExifTags.Base.ISOSpeedRatings), _ISO_RANGE),
        exposure_bias=_in_range(
            _number(exif_ifd.get(ExifTags.Base.ExposureBiasValue)),
            _EXPOSURE_BIAS_RANGE,
        ),
        flash_fired=_flash_fired(exif_ifd.get(ExifTags.Base.Flash)),
        latitude=latitude,
        longitude=longitude,
        altitude=altitude,
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


def _number(value):
    """A finite float from an EXIF numeric value, or None.

    Pillow hands rationals over as ``IFDRational``, which reads as NaN when
    its denominator is zero. Some writers store a count of one as a
    one-element tuple.
    """
    if isinstance(value, tuple) and len(value) == 1:
        value = value[0]
    if isinstance(value, bool) or not isinstance(value, (int, float, Rational)):
        return None
    try:
        number = float(value)
    except ArithmeticError, TypeError, ValueError:
        return None
    return number if math.isfinite(number) else None


def _in_range(number, bounds):
    if number is None:
        return None
    low, high = bounds
    return number if low <= number <= high else None


def _integer(value, bounds):
    """An EXIF SHORT/LONG as an int inside *bounds*. ISO is a list on some
    cameras (one value per sensitivity type); the first one is the ISO."""
    if isinstance(value, tuple) and value:
        value = value[0]
    number = _in_range(_number(value), bounds)
    return round(number) if number is not None else None


def _flash_fired(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value & _NO_FLASH_FUNCTION:
        return None
    return bool(value & _FLASH_FIRED)


def _position(gps_ifd):
    """``(latitude, longitude, altitude)`` from the GPS IFD, in signed decimal
    degrees and metres; all None when the file carries no usable fix.

    Out-of-range values and the ``0, 0`` some cameras write when they had no
    fix are dropped. The altitude is only kept alongside a position.
    """
    latitude = _coordinate(
        gps_ifd.get(ExifTags.GPS.GPSLatitude),
        gps_ifd.get(ExifTags.GPS.GPSLatitudeRef),
        positive="N",
        negative="S",
        limit=90,
    )
    longitude = _coordinate(
        gps_ifd.get(ExifTags.GPS.GPSLongitude),
        gps_ifd.get(ExifTags.GPS.GPSLongitudeRef),
        positive="E",
        negative="W",
        limit=180,
    )
    if latitude is None or longitude is None or (latitude == 0 and longitude == 0):
        return None, None, None
    return latitude, longitude, _altitude(gps_ifd)


def _coordinate(raw, raw_ref, *, positive, negative, limit):
    """Degrees, minutes and seconds plus an N/S or E/W ref, as signed degrees.

    A writer that leaves the ref out gets its value as is; one that writes
    anything but the two expected letters is not trusted with the rest.
    """
    if not isinstance(raw, tuple) or not 1 <= len(raw) <= 3:
        return None
    parts = [_number(part) for part in raw]
    if any(part is None or part < 0 for part in parts):
        return None
    degrees = sum(part / 60**index for index, part in enumerate(parts))

    ref = _text(raw_ref).upper()
    if ref == negative:
        degrees = -degrees
    elif ref not in ("", positive):
        return None
    return degrees if -limit <= degrees <= limit else None


def _altitude(gps_ifd):
    altitude = _number(gps_ifd.get(ExifTags.GPS.GPSAltitude))
    if altitude is None:
        return None
    # GPSAltitudeRef is a BYTE: 1 means below sea level. Pillow returns it as
    # bytes or as an int depending on how the file was written.
    ref = gps_ifd.get(ExifTags.GPS.GPSAltitudeRef)
    below_sea_level = ref[:1] == b"\x01" if isinstance(ref, bytes) else ref == 1
    if below_sea_level:
        altitude = -altitude
    return _in_range(altitude, _ALTITUDE_RANGE)
