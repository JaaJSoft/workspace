"""What the Photo section of the Files properties panel says about a photo.

The panel belongs to ``files``; photos contributes the section through
``files.properties_sections`` (registered in ``PhotosConfig.ready()``).
"""

from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlencode

from ..models import MediaItem

# Below this, a shutter speed reads as a fraction of a second ("1/4 s",
# "1/120 s"); from it up, as a decimal ("0.3 s", "2 s"), the way cameras
# display them.
_FRACTION_SHUTTER_LIMIT = 0.3
_OPENSTREETMAP_URL = "https://www.openstreetmap.org/"
_OPENSTREETMAP_ZOOM = 16
# Five decimals is about a metre: finer than any phone's GPS fix.
_COORDINATE_DECIMALS = 5


@dataclass(frozen=True)
class PhotoDetails:
    taken_at: datetime | None = None
    dimensions: str = ""
    megapixels: str = ""
    camera: str = ""
    lens: str = ""
    exposure: list[str] = field(default_factory=list)
    coordinates: str = ""
    altitude: str = ""
    map_url: str = ""

    def __bool__(self):
        return bool(
            self.taken_at
            or self.dimensions
            or self.camera
            or self.lens
            or self.exposure
            or self.coordinates
        )


def current_photo_item(file_obj):
    """The MediaItem of *file_obj* when it describes it as a photo in its
    current content, or None.

    A row describing replaced bytes is not shown: until the analysis catches
    up, its camera and place are those of another picture.
    """
    try:
        item = file_obj.media_item
    except MediaItem.DoesNotExist:
        return None
    if item.media_type != MediaItem.MediaType.PHOTO:
        return None
    if item.content_hash != file_obj.content_hash:
        return None
    return item


def photo_details(item):
    """The :class:`PhotoDetails` of a photo's MediaItem row."""
    details = {}
    if item.width and item.height:
        details["dimensions"] = f"{item.width} × {item.height}"
        megapixels = item.width * item.height / 1_000_000
        if megapixels >= 0.1:
            details["megapixels"] = f"{megapixels:.1f} MP"
    if item.latitude is not None and item.longitude is not None:
        details.update(_location(item))
    return PhotoDetails(
        taken_at=item.taken_at,
        camera=_brand_and_name(item.camera_make, item.camera_model),
        lens=_brand_and_name(item.lens_make, item.lens_model),
        exposure=_exposure(item),
        **details,
    )


def _brand_and_name(make, model):
    """Brand and model as one name ("Apple iPhone 15 Pro"), without the
    brand twice when the model already carries it: "NIKON CORPORATION" and
    "NIKON D850" make a "NIKON D850"."""
    make, model = (make or "").strip(), (model or "").strip()
    if not model:
        return ""
    brand = make.split(" ", 1)[0].lower()
    if not brand or model.lower().startswith(brand):
        return model
    return f"{make} {model}"


def _decimal(value, digits=1):
    return f"{round(value, digits):g}"


def _exposure(item):
    """The settings a photographer reads off a shot, in the usual order:
    aperture, shutter speed, ISO, focal length, then the exceptions."""
    parts = []
    if item.f_number is not None:
        parts.append(f"f/{_decimal(item.f_number)}")
    if item.exposure_time is not None:
        parts.append(_shutter_speed(item.exposure_time))
    if item.iso is not None:
        parts.append(f"ISO {item.iso}")
    focal = _focal_length(item.focal_length, item.focal_length_35mm)
    if focal:
        parts.append(focal)
    if item.exposure_bias is not None and round(item.exposure_bias, 1) != 0:
        parts.append(f"{round(item.exposure_bias, 1):+g} EV")
    if item.flash_fired:
        parts.append("Flash")
    return parts


def _shutter_speed(seconds):
    if seconds < _FRACTION_SHUTTER_LIMIT:
        return f"1/{round(1 / seconds)} s"
    return f"{_decimal(seconds)} s"


def _focal_length(focal_length, focal_length_35mm):
    """The lens's focal length, with the full-frame equivalent next to it
    when the sensor is not full frame (a phone's 6.8 mm is a 26 mm)."""
    if focal_length is None and focal_length_35mm is None:
        return ""
    if focal_length is None:
        return f"{focal_length_35mm} mm (35 mm eq.)"
    actual = f"{_decimal(focal_length)} mm"
    if focal_length_35mm is None or focal_length_35mm == round(focal_length):
        return actual
    return f"{actual} ({focal_length_35mm} mm eq.)"


def _location(item):
    latitude = f"{item.latitude:.{_COORDINATE_DECIMALS}f}"
    longitude = f"{item.longitude:.{_COORDINATE_DECIMALS}f}"
    query = urlencode({"mlat": latitude, "mlon": longitude})
    location = {
        "coordinates": f"{latitude}, {longitude}",
        "map_url": (
            f"{_OPENSTREETMAP_URL}?{query}"
            f"#map={_OPENSTREETMAP_ZOOM}/{latitude}/{longitude}"
        ),
    }
    if item.altitude is not None:
        location["altitude"] = f"{round(item.altitude)} m"
    return location


def is_section_visible(user, file_obj):
    item = current_photo_item(file_obj)
    return item is not None and bool(photo_details(item))


def section_context(user, file_obj):
    return {"photo": photo_details(current_photo_item(file_obj))}
