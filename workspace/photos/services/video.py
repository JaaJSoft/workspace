"""Read the recording metadata of a video: date, size, camera, place.

Its length and codecs are a property of the file, not of the library: the
files module probes them (``files.services.media_info``).

ffprobe reads the container header only, so a long recording costs about as
much to analyze as a short one.

The recording date comes, in order of preference, from:

- QuickTime's ``com.apple.quicktime.creationdate``, the camera's wall clock
  with its UTC offset: the exact instant, like EXIF with OffsetTimeOriginal;
- the container's ``creation_time``, UTC by specification. Some cameras
  write their local time there instead, and nothing in the file tells the two
  apart: such a video lands a few hours off, on the neighbouring day at worst,
  which beats sending every video without an Apple tag to Undated.

Muxers with no clock write the epochs of their formats (1904 for QuickTime,
1970 for Unix) instead of leaving the field out; those read as no date.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from django.utils import timezone

from workspace.files.services import ffmpeg

from .exif import CAMERA_FIELD_LENGTH

_APPLE = "com.apple.quicktime."
_CREATION_DATE = _APPLE + "creationdate"
_CREATION_TIME = "creation_time"
# ISO 6709 as phones write it: "+48.8584+002.2945+035.000/". Only the
# latitude and longitude are read; the altitude, when there is one, follows.
_ISO6709_RE = re.compile(
    r"^(?P<lat>[+-]\d{1,2}(?:\.\d+)?)(?P<lon>[+-]\d{1,3}(?:\.\d+)?)"
)
_LOCATION_KEYS = (_APPLE + "location.iso6709", "location", "location-eng")
_MAKE_KEYS = (_APPLE + "make", "com.android.manufacturer", "make")
_MODEL_KEYS = (_APPLE + "model", "com.android.model", "model")
_EARLIEST_RECORDING = datetime(1971, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class VideoMetadata:
    taken_at: datetime | None = None
    width: int | None = None
    height: int | None = None
    camera_make: str = ""
    camera_model: str = ""
    latitude: float | None = None
    longitude: float | None = None


def read_metadata(path, *, default_tz=UTC):
    """Return the :class:`VideoMetadata` of the video file at *path*.

    Raises :class:`ffmpeg.MediaToolError` when ffprobe is missing or cannot
    make sense of the file.
    """
    return parse_report(ffmpeg.probe(path), default_tz=default_tz)


def parse_report(report, *, default_tz=UTC):
    """The :class:`VideoMetadata` an ffprobe JSON report describes.

    *default_tz* reads a QuickTime creation date written without an offset.
    """
    tags = _lowercase_keys(report.get("format", {}).get("tags"))
    stream = ffmpeg.video_stream(report)
    stream_tags = _lowercase_keys(stream.get("tags"))

    width, height = _positive(stream.get("width")), _positive(stream.get("height"))
    if width and height and _rotation(stream) % 180 == 90:
        width, height = height, width

    latitude, longitude = _location(_first(tags, _LOCATION_KEYS))
    return VideoMetadata(
        taken_at=_recording_time(tags, stream_tags, default_tz),
        width=width,
        height=height,
        camera_make=_first(tags, _MAKE_KEYS)[:CAMERA_FIELD_LENGTH],
        camera_model=_first(tags, _MODEL_KEYS)[:CAMERA_FIELD_LENGTH],
        latitude=latitude,
        longitude=longitude,
    )


def _lowercase_keys(tags):
    if not isinstance(tags, dict):
        return {}
    return {str(key).lower(): value for key, value in tags.items()}


def _first(tags, keys):
    for key in keys:
        value = tags.get(key)
        if isinstance(value, str) and value.strip():
            return value.replace("\x00", "").strip()
    return ""


def _positive(value):
    return value if isinstance(value, int) and value > 0 else None


def _rotation(stream):
    """The rotation a player applies, in degrees, from the display matrix
    (current ffmpeg) or the ``rotate`` tag (older muxers)."""
    for side_data in stream.get("side_data_list") or []:
        if isinstance(side_data, dict) and "rotation" in side_data:
            return _degrees(side_data["rotation"])
    return _degrees(_lowercase_keys(stream.get("tags")).get("rotate"))


def _degrees(value):
    try:
        return round(float(value)) % 360
    except TypeError, ValueError:
        return 0


def _recording_time(tags, stream_tags, default_tz):
    apple = _parse_time(_first(tags, (_CREATION_DATE,)), default_tz)
    if apple is not None:
        return apple
    for source in (tags, stream_tags):
        instant = _parse_time(_first(source, (_CREATION_TIME,)), UTC)
        if instant is not None:
            return instant
    return None


def _parse_time(raw, default_tz):
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, default_tz)
    if parsed < _EARLIEST_RECORDING:
        return None
    return parsed


def _location(raw):
    """Signed decimal degrees from an ISO 6709 string, or ``(None, None)``.

    Out-of-range values and the ``0, 0`` some devices write when they had no
    fix are dropped.
    """
    match = _ISO6709_RE.match(raw)
    if match is None:
        return None, None
    latitude, longitude = float(match["lat"]), float(match["lon"])
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None, None
    if latitude == 0 and longitude == 0:
        return None, None
    return latitude, longitude
