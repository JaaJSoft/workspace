"""The projects module's user settings."""

from rest_framework import serializers

from workspace.core.sidebar_settings import SidebarSettings

from .services.notification_levels import Level


class ProjectsSettings(SidebarSettings):
    # The local hour the due-task reminder is sent at.
    reminder_hour = serializers.IntegerField(min_value=0, max_value=23)
    notify_level = serializers.ChoiceField(choices=Level.choices)
    auto_watch = serializers.BooleanField()
    last_project = serializers.UUIDField(read_only=True)
