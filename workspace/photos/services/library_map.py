"""The library map: every located photo of a library as a point the browser
groups by what lands close together on screen (photos/ui/js/library_map.js),
and the photos of the area on screen, for the grid beside it.

Offered where the photo's own map is (services/location_map.py): the instance
names a tile server, and the user turned the ``photos`` / ``show_map``
setting on.
"""

import math
from dataclasses import dataclass
from datetime import datetime

from django.db.models import F, Q

from workspace.files.services.image_edits import edit_version

from ..models import MediaItem
from .shared_location import location_shown_q

# Decimal places kept on a point's coordinates: about a metre.
COORDINATE_PLACES = 5


def located(files, viewer):
    """The files of *files* whose MediaItem carries a position *viewer* may
    learn: the owner of a photo shared with them may keep it to themselves
    (services/shared_location.py)."""
    return files.filter(
        location_shown_q(viewer),
        media_item__latitude__isnull=False,
        media_item__longitude__isnull=False,
    )


def month_index(moment, tz):
    """The month *moment* falls in, in *tz*, counted from year 0: what the
    map's period filter steps through, one month at a time."""
    local = moment.astimezone(tz)
    return local.year * 12 + local.month - 1


def map_points(files, tz, viewer):
    """The located files of *files* as points, newest first, undated last.

    Each point is a list, so a library of tens of thousands of photos stays
    a small download: ``[uuid, latitude, longitude, month, is_video,
    sensitive_level, thumbnail]``. ``month`` is :func:`month_index`, None
    for an undated photo; ``thumbnail`` is None while the file has none,
    else the edit version its URL carries ("" for an unedited image).
    """
    rows = (
        located(files, viewer)
        .order_by(F("media_item__taken_at").desc(nulls_last=True), "-uuid")
        .values_list(
            "uuid",
            "media_item__latitude",
            "media_item__longitude",
            "media_item__taken_at",
            "media_item__media_type",
            "media_item__sensitive_level",
            "has_thumbnail",
            "image_edit",
        )
    )
    return [
        [
            str(uuid),
            round(latitude, COORDINATE_PLACES),
            round(longitude, COORDINATE_PLACES),
            month_index(taken_at, tz) if taken_at is not None else None,
            int(media_type == MediaItem.MediaType.VIDEO),
            sensitive_level,
            edit_version(image_edit) if has_thumbnail else None,
        ]
        for (
            uuid,
            latitude,
            longitude,
            taken_at,
            media_type,
            sensitive_level,
            has_thumbnail,
            image_edit,
        ) in rows
    ]


@dataclass(frozen=True)
class Area:
    """A box on the map, in degrees. *west* is east of *east* when the box
    spans the antimeridian."""

    west: float
    south: float
    east: float
    north: float


def parse_area(raw):
    """The :class:`Area` of a ``west,south,east,north`` string, as Leaflet's
    ``toBBoxString`` writes it.

    Longitudes are wrapped into [-180, 180]; a box at least a world wide
    takes every longitude. ValueError on anything else than four finite
    numbers with the south below the north.
    """
    parts = raw.split(",")
    if len(parts) != 4:
        raise ValueError("An area is four numbers.")
    west, south, east, north = (float(part) for part in parts)
    if not all(math.isfinite(value) for value in (west, south, east, north)):
        raise ValueError("An area is four finite numbers.")
    if south > north:
        raise ValueError("The south of an area is below its north.")
    if east - west >= 360:
        return Area(-180.0, south, 180.0, north)
    return Area(_wrap(west), south, _wrap(east), north)


def _wrap(longitude):
    # 180 stays 180: a box ending on the antimeridian does not wrap to -180.
    if -180 <= longitude <= 180:
        return longitude
    return (longitude + 180) % 360 - 180


def in_area(files, area, viewer):
    """The files of *files* placed inside *area*, as *viewer* may see them."""
    longitude = (
        Q(media_item__longitude__gte=area.west, media_item__longitude__lte=area.east)
        if area.west <= area.east
        else Q(media_item__longitude__gte=area.west)
        | Q(media_item__longitude__lte=area.east)
    )
    return located(files, viewer).filter(
        longitude,
        media_item__latitude__gte=area.south,
        media_item__latitude__lte=area.north,
    )


def parse_month(raw):
    """The :func:`month_index` of a ``YYYY-MM`` string. ValueError otherwise."""
    year, separator, month = raw.partition("-")
    if not (separator and len(year) == 4 and len(month) == 2):
        raise ValueError("A month is YYYY-MM.")
    year, month = int(year), int(month)
    # A period ends where the month after its last begins: year 9999 has none.
    if not (1 <= year <= 9998 and 1 <= month <= 12):
        raise ValueError("No such month.")
    return year * 12 + month - 1


def month_token(index):
    """The ``YYYY-MM`` string of a :func:`month_index`."""
    year, month = divmod(index, 12)
    return f"{year:04d}-{month + 1:02d}"


def _month_start(index, tz):
    year, month = divmod(index, 12)
    return datetime(year, month + 1, 1, tzinfo=tz)


def in_period(files, first, last, tz):
    """The files of *files* taken from month *first* to month *last*
    (:func:`month_index`, both included), in *tz*. Undated files are out."""
    return files.filter(
        media_item__taken_at__gte=_month_start(first, tz),
        media_item__taken_at__lt=_month_start(last + 1, tz),
    )
