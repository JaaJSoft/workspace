"""The dashboard preferences a user sets in the header's Preferences popover.

Each is a ``dashboard`` user setting, declared in ``dashboard/user_settings.py``:
the API refuses a malformed value. Every reader here falls back to the
default on one anyway: a value stored before the rule existed must not break
the page.
"""

MODULE = "dashboard"

# Slugs of the Today card's sources the user switched off.
HIDDEN_TODAY_SOURCES = "hidden_today_sources"

# Slugs of the modules whose tile the user took off the grid.
HIDDEN_MODULES = "hidden_modules"

TEMPERATURE_UNIT = "temperature_unit"

CELSIUS = "celsius"
FAHRENHEIT = "fahrenheit"
# Open-Meteo's ``temperature_unit`` values, with the symbol the picker shows.
TEMPERATURE_UNITS = {CELSIUS: "°C", FAHRENHEIT: "°F"}


def temperature_unit(dashboard_settings):
    """The unit the weather line reads in, from the module's settings map."""
    value = dashboard_settings.get(TEMPERATURE_UNIT)
    return value if value in TEMPERATURE_UNITS else CELSIUS


def is_slug_list(value):
    """Whether *value* can be stored as one of the two lists of slugs."""
    return isinstance(value, list) and all(isinstance(slug, str) for slug in value)


def hidden_today_sources(dashboard_settings):
    """Slugs of the Today sources switched off; none on a malformed value."""
    value = dashboard_settings.get(HIDDEN_TODAY_SOURCES)
    return set(value) if is_slug_list(value) else set()


def hidden_modules(dashboard_settings):
    """Slugs of the modules taken off the grid; none on a malformed value."""
    value = dashboard_settings.get(HIDDEN_MODULES)
    return set(value) if is_slug_list(value) else set()
