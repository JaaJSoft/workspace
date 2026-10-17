"""The map of a photo's location in its properties panel.

Opt-in twice: the instance names a tile server (PHOTOS_MAP_TILE_URL and the
attribution its policy requires), and the user turned the map on (the
``photos`` / ``show_map`` user setting). Until both, the Photo section shows
the coordinates and a link, nothing is fetched from the tile server.
"""

from dataclasses import dataclass
from urllib.parse import urlsplit

from django.conf import settings

from .preferences import show_map

# Street level: close enough to read the streets around the pin.
MAP_ZOOM = 16


@dataclass(frozen=True)
class LocationMap:
    # The host of each tile server, named to the user before they opt in.
    server: str
    enabled: bool
    tile_url: str = ""
    tile_url_dark: str = ""
    attribution: str = ""
    latitude: float | None = None
    longitude: float | None = None
    zoom: int = MAP_ZOOM


def map_available():
    """Whether this instance offers the map at all."""
    return bool(settings.PHOTOS_MAP_TILE_URL and settings.PHOTOS_MAP_TILE_ATTRIBUTION)


def tile_server():
    """The tile servers' hosts, without the ``{s}`` subdomain placeholder:
    "a.example" or, with a dark style elsewhere, "a.example and b.example"."""
    urls = (settings.PHOTOS_MAP_TILE_URL, settings.PHOTOS_MAP_TILE_URL_DARK)
    hosts = dict.fromkeys(
        (urlsplit(url).hostname or "").removeprefix("{s}.") for url in urls if url
    )
    return " and ".join(host for host in hosts if host)


def location_map(user, item):
    """The :class:`LocationMap` of a photo's MediaItem, or None when there is
    no map to offer: no tile server, or no position."""
    if not map_available() or item.latitude is None or item.longitude is None:
        return None
    if not show_map(user):
        return LocationMap(server=tile_server(), enabled=False)
    return LocationMap(
        server=tile_server(),
        enabled=True,
        tile_url=settings.PHOTOS_MAP_TILE_URL,
        tile_url_dark=settings.PHOTOS_MAP_TILE_URL_DARK,
        attribution=settings.PHOTOS_MAP_TILE_ATTRIBUTION,
        latitude=item.latitude,
        longitude=item.longitude,
    )
