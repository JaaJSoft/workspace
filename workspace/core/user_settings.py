"""The user settings of the shell: appearance, region, search, sensitive blur."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from rest_framework import serializers

from workspace.files.sensitivity import BlurPreference
from workspace.users.settings_registry import not_a_choice

from .module_registry import SearchScope


def _theme():
    # A daisyUI theme name, written into <html data-theme>.
    return serializers.RegexField(r"^[a-z0-9-]+$", max_length=40)


class CoreSettings(serializers.Serializer):
    theme = _theme()
    light_theme = _theme()
    dark_theme = _theme()
    timezone = serializers.CharField(max_length=64)
    language = serializers.ChoiceField(choices=settings.LANGUAGES)
    search_scope = serializers.ChoiceField(choices=SearchScope.CHOICES)
    sensitive_blur = serializers.ChoiceField(choices=BlurPreference.choices)
    reveal_sensitive_on_hover = serializers.BooleanField()
    reveal_sensitive_in_viewer = serializers.BooleanField()
    onboarding_completed = serializers.BooleanField()
    changelog_last_seen_version = serializers.CharField(read_only=True)

    def validate_timezone(self, value):
        try:
            ZoneInfo(value)
        # OSError: a key naming a directory of the database ("Europe").
        except ZoneInfoNotFoundError, ValueError, OSError:
            raise not_a_choice(value) from None
        return value
