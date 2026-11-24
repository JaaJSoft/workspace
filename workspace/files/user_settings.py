"""The file browser's user settings."""

from rest_framework import serializers

from workspace.core.sidebar_settings import SidebarSettings
from workspace.users.settings_registry import Preferences

VIEW_MODES = ("list", "mosaic")
_SORT_DIRECTIONS = ("asc", "desc")
_MAX_COLUMNS = 64


def _column():
    return serializers.CharField(max_length=64)


class FilePreferences(Preferences):
    """The Preferences panel and the listing's view toggle."""

    showHiddenFiles = serializers.BooleanField()
    confirmBeforeDelete = serializers.BooleanField()
    defaultSort = serializers.ChoiceField(
        choices=("default", "name", "modified", "created", "size", "type")
    )
    defaultSortDir = serializers.ChoiceField(choices=_SORT_DIRECTIONS)
    breadcrumbCollapse = serializers.IntegerField(min_value=2, max_value=8)
    defaultViewMode = serializers.ChoiceField(choices=VIEW_MODES)
    mosaicTileSize = serializers.IntegerField(min_value=1, max_value=5)
    showPinned = serializers.BooleanField()
    showGroups = serializers.BooleanField()
    showTags = serializers.BooleanField()
    compactList = serializers.BooleanField()
    nameCollision = serializers.ChoiceField(
        choices=("ask", "keep_both", "replace", "skip")
    )


class TableControls(Preferences):
    """The listing's columns, as the table left them. The sort is not kept:
    a listing opens on the Default sort preference."""

    columnOrder = serializers.ListField(child=_column(), max_length=_MAX_COLUMNS)
    columnVisibility = serializers.DictField(child=serializers.BooleanField())

    def validate_columnVisibility(self, value):
        if len(value) > _MAX_COLUMNS or any(len(column) > 64 for column in value):
            message = serializers.ListField.default_error_messages["max_length"]
            raise serializers.ValidationError(message.format(max_length=_MAX_COLUMNS))
        return value


class EditorPreferences(Preferences):
    """The text viewer's editor options."""

    wordWrap = serializers.BooleanField()
    minimap = serializers.BooleanField()
    fontSize = serializers.IntegerField(min_value=8, max_value=32)
    tabSize = serializers.IntegerField(min_value=1, max_value=8)
    showWhitespace = serializers.BooleanField()
    lineNumbers = serializers.BooleanField()


class FilesSettings(SidebarSettings):
    preferences = FilePreferences()
    table_controls = TableControls()
    editor = EditorPreferences()
