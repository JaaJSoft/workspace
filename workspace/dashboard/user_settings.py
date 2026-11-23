"""The dashboard's user settings (read through services/preferences.py)."""

from rest_framework import serializers

from .services.preferences import TEMPERATURE_UNITS


def _slugs():
    return serializers.ListField(
        child=serializers.SlugField(max_length=64), max_length=64
    )


class DashboardSettings(serializers.Serializer):
    hidden_today_sources = _slugs()
    hidden_modules = _slugs()
    temperature_unit = serializers.ChoiceField(choices=list(TEMPERATURE_UNITS))
