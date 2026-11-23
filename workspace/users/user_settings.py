"""The settings of a user's public profile, stored under ``profile``."""

from rest_framework import serializers

from .banner_palettes import BANNER_PALETTES, HEX_COLOUR
from .settings_registry import not_a_choice


class _CustomPalette(serializers.Serializer):
    def get_fields(self):
        # "from" is a keyword, so the stops cannot be class attributes.
        return {
            stop: serializers.RegexField(HEX_COLOUR) for stop in ("from", "via", "to")
        }


class BannerPaletteField(serializers.Field):
    """A preset of BANNER_PALETTES, or three colours of the user's choosing."""

    def to_internal_value(self, data):
        if isinstance(data, str):
            if data not in BANNER_PALETTES:
                raise not_a_choice(data)
            return data
        return _CustomPalette().run_validation(data)

    def to_representation(self, value):
        return value


class ProfileSettings(serializers.Serializer):
    bio = serializers.CharField(max_length=200)
    role = serializers.CharField(max_length=50)
    banner_palette = BannerPaletteField()
    has_avatar = serializers.BooleanField(read_only=True)
