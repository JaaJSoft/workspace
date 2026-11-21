"""The dashboard preferences a user sets in the header's Preferences popover.

Each is a ``dashboard`` user setting. The API refuses a malformed value (see
``_validate_setting_value`` in users/views.py), and every reader here falls
back to the default on one anyway: a value stored before the rule existed
must not break the page.
"""

MODULE = "dashboard"

# Slugs of the Today card's sources the user switched off.
HIDDEN_TODAY_SOURCES = "hidden_today_sources"

TEMPERATURE_UNIT = "temperature_unit"

CELSIUS = "celsius"
FAHRENHEIT = "fahrenheit"
# Open-Meteo's ``temperature_unit`` values, with the symbol the picker shows.
TEMPERATURE_UNITS = {CELSIUS: "°C", FAHRENHEIT: "°F"}


def temperature_unit(dashboard_settings):
    """The unit the weather line reads in, from the module's settings map."""
    value = dashboard_settings.get(TEMPERATURE_UNIT)
    return value if value in TEMPERATURE_UNITS else CELSIUS
