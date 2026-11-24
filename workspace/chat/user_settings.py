"""The chat module's user settings."""

from rest_framework import serializers

from workspace.core.sidebar_settings import SidebarSettings
from workspace.users.settings_registry import Preferences


class ChatPreferences(Preferences):
    compactConversationList = serializers.BooleanField()
    compactMessageView = serializers.BooleanField()
    showThreadRepliesInline = serializers.BooleanField()
    messageAnimation = serializers.ChoiceField(
        choices=("slide", "pop", "fade", "bounce", "none")
    )
    composerMode = serializers.ChoiceField(choices=("rendered", "markdown"))


class ChatSettings(SidebarSettings):
    preferences = ChatPreferences()
    call_sounds = serializers.BooleanField()
