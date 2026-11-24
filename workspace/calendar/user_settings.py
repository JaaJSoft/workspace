"""The calendar module's user settings."""

from rest_framework import serializers

from workspace.core.sidebar_settings import SidebarSettings
from workspace.users.settings_registry import Preferences


class CalendarPreferences(Preferences):
    defaultView = serializers.ChoiceField(
        choices=("dayGridMonth", "timeGridWeek", "timeGridDay", "agenda")
    )
    # 0 is Sunday, as FullCalendar counts.
    firstDay = serializers.IntegerField(min_value=0, max_value=6)
    weekNumbers = serializers.BooleanField()
    defaultAllDay = serializers.BooleanField()
    showDeclined = serializers.BooleanField()
    notifyPollVotes = serializers.BooleanField()
    showTasks = serializers.BooleanField()


class CalendarSettings(SidebarSettings):
    preferences = CalendarPreferences()
