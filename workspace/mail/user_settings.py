"""The mail module's user settings."""

from rest_framework import serializers

from workspace.core.sidebar_settings import SidebarSettings
from workspace.users.settings_registry import Preferences

from .services.ai_settings import MAIL_AI_FEATURES
from .services.notifications import HARD_MAX_NOTIFY_BURST, NOTIFY_MODES


class MailPreferences(Preferences):
    density = serializers.ChoiceField(choices=("compact", "normal", "spacious"))
    previewLines = serializers.IntegerField(min_value=0, max_value=2)
    confirmBeforeDelete = serializers.BooleanField()
    showLabels = serializers.BooleanField()
    rulesCompact = serializers.BooleanField()


class MailSettings(SidebarSettings):
    preferences = MailPreferences()
    notify_mode = serializers.ChoiceField(choices=NOTIFY_MODES)
    notify_max_burst = serializers.IntegerField(
        min_value=1, max_value=HARD_MAX_NOTIFY_BURST
    )

    def get_fields(self):
        fields = super().get_fields()
        # One switch per AI feature: ai_classify, ai_extract, ai_manual.
        for feature in MAIL_AI_FEATURES:
            fields[f"ai_{feature}"] = serializers.BooleanField()
        return fields
