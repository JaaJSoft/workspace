"""Weather lookup for AI tools.

Mirrors the dashboard weather widget (Open-Meteo, WMO weather codes) but
resolves a free-text place name to coordinates first, so a bot can answer
"what's the weather in Tokyo?" without any browser geolocation.

Both APIs are free and keyless:
- Geocoding:  https://geocoding-api.open-meteo.com/v1/search
- Forecast:   https://api.open-meteo.com/v1/forecast
"""

import logging

import httpx

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes -> human-readable description.
# Same code set the dashboard widget maps to icons (index.html).
_WEATHER_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snowfall",
    73: "Moderate snowfall",
    75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def describe_weather_code(code) -> str:
    """Return a human-readable description for a WMO weather code."""
    try:
        return _WEATHER_CODES.get(int(code), "Unknown conditions")
    except TypeError, ValueError:
        return "Unknown conditions"


def geocode(name: str) -> dict | None:
    """Resolve a place name to coordinates via Open-Meteo geocoding.

    Returns a dict with ``name``, ``country``, ``latitude`` and ``longitude``,
    or ``None`` when the place is unknown or the request fails.
    """
    query = name.strip()
    if not query:
        return None

    try:
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            resp = client.get(
                _GEOCODE_URL,
                params={
                    "name": query,
                    "count": 1,
                    "language": "en",
                    "format": "json",
                },
            )
            resp.raise_for_status()
    except httpx.HTTPError:
        logger.exception("Geocoding failed for place: %.80s", scrub(query))
        return None

    results = resp.json().get("results") or []
    if not results:
        return None

    top = results[0]
    parts = [top.get("name"), top.get("admin1"), top.get("country")]
    label = ", ".join(p for p in parts if p)
    return {
        "name": label or top.get("name", query),
        "country": top.get("country", ""),
        "latitude": top.get("latitude"),
        "longitude": top.get("longitude"),
    }


def get_current_weather(name: str) -> dict | None:
    """Return current weather for a named place.

    Geocodes *name*, then fetches the current conditions from Open-Meteo.
    Returns a dict describing the location and conditions, or ``None`` when
    the place cannot be resolved or the forecast request fails.
    """
    place = geocode(name)
    if place is None:
        return None

    try:
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            resp = client.get(
                _FORECAST_URL,
                params={
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "current": (
                        "temperature_2m,apparent_temperature,"
                        "relative_humidity_2m,wind_speed_10m,weather_code,is_day"
                    ),
                },
            )
            resp.raise_for_status()
    except httpx.HTTPError:
        logger.exception("Forecast failed for place: %.80s", scrub(name))
        return None

    payload = resp.json()
    current = payload.get("current") or {}
    units = payload.get("current_units") or {}
    code = current.get("weather_code")

    return {
        "location": place["name"],
        "temperature": current.get("temperature_2m"),
        "temperature_unit": units.get("temperature_2m", "°C"),
        "feels_like": current.get("apparent_temperature"),
        "humidity": current.get("relative_humidity_2m"),
        "humidity_unit": units.get("relative_humidity_2m", "%"),
        "wind_speed": current.get("wind_speed_10m"),
        "wind_speed_unit": units.get("wind_speed_10m", "km/h"),
        "conditions": describe_weather_code(code),
        "weather_code": code,
        "is_day": bool(current.get("is_day", 1)),
    }
