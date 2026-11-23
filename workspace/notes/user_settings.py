"""The notes module's user settings."""

from rest_framework import serializers

from workspace.core.sidebar_settings import SidebarSettings
from workspace.users.settings_registry import Preferences


class NotesPreferences(Preferences):
    showTags = serializers.BooleanField()
    compactList = serializers.BooleanField()
    showFolders = serializers.BooleanField()
    showJournal = serializers.BooleanField()
    showGroupFolders = serializers.BooleanField()
    defaultView = serializers.ChoiceField(choices=("all", "recent", "journal"))
    sortBy = serializers.ChoiceField(choices=("modified", "created", "name"))
    confirmBeforeDelete = serializers.BooleanField()
    hiddenItems = serializers.ListField(child=serializers.UUIDField(), max_length=2000)
    showHidden = serializers.BooleanField()
    # Checked against the user's own folders where they are read (ui/views.py).
    defaultFolderUuid = serializers.UUIDField()
    journalFolderUuid = serializers.UUIDField()


class NotesSettings(SidebarSettings):
    preferences = NotesPreferences()
