"""The photos module's user settings (read through services/preferences.py)."""

from rest_framework import serializers

from workspace.core.sidebar_settings import SidebarSettings
from workspace.users.settings_registry import not_a_choice

from .models import MediaItem
from .services.preferences import ALL_TYPES, TILE_SHAPES, is_scope_token


class PhotosSettings(SidebarSettings):
    # A step of the timeline's size slider.
    tile_size = serializers.IntegerField(min_value=1, max_value=5)
    tile_shape = serializers.ChoiceField(choices=TILE_SHAPES)
    tile_badges = serializers.BooleanField()
    video_hover_preview = serializers.BooleanField()
    default_scope = serializers.CharField(max_length=32)
    default_media_type = serializers.ChoiceField(
        choices=(ALL_TYPES, *MediaItem.MediaType.values)
    )
    import_by_date = serializers.BooleanField()
    show_hidden = serializers.BooleanField()
    auto_tags = serializers.BooleanField()
    show_map = serializers.BooleanField()
    faces_enabled = serializers.BooleanField()
    # Set through the import folder endpoint, which checks the folder.
    import_folder = serializers.UUIDField(read_only=True)

    def validate_default_scope(self, value):
        if not is_scope_token(value):
            raise not_a_choice(value)
        return value
