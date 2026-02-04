from . import ActionRegistry
from .base import ActionCategory, BaseAction


@ActionRegistry.register
class ToggleFavoriteAction(BaseAction):
    id = 'toggle_favorite'
    label = 'Add to favorites'
    icon = 'star'
    category = ActionCategory.ORGANIZE
    node_types = ('file', 'folder')
    keyboard_shortcut = 'F'
    supports_bulk = True

    def is_available(self, user, file_obj, *, is_owner, share_permission=None):
        if file_obj.deleted_at is not None:
            return False
        return is_owner or share_permission is not None

    def get_label(self, file_obj):
        if getattr(file_obj, 'is_favorite', False):
            return 'Remove from favorites'
        return 'Add to favorites'

    def get_icon(self, file_obj):
        return 'star'

    def serialize(self, file_obj):
        data = super().serialize(file_obj)
        data['state'] = {'is_favorite': bool(getattr(file_obj, 'is_favorite', False))}
        return data


@ActionRegistry.register
class TogglePinAction(BaseAction):
    id = 'toggle_pin'
    label = 'Pin to sidebar'
    icon = 'pin'
    category = ActionCategory.ORGANIZE
    node_types = ('folder',)
    keyboard_shortcut = 'P'
    supports_bulk = True

    def is_available(self, user, file_obj, *, is_owner, share_permission=None):
        if file_obj.deleted_at is not None:
            return False
        return is_owner

    def get_label(self, file_obj):
        if getattr(file_obj, 'is_pinned', False):
            return 'Unpin from sidebar'
        return 'Pin to sidebar'

    def get_icon(self, file_obj):
        return 'pin'

    def serialize(self, file_obj):
        data = super().serialize(file_obj)
        data['state'] = {'is_pinned': bool(getattr(file_obj, 'is_pinned', False))}
        return data


@ActionRegistry.register
class ShareAction(BaseAction):
    id = 'share'
    label = 'Share'
    icon = 'share-2'
    category = ActionCategory.ORGANIZE
    node_types = ('file',)

    def is_available(self, user, file_obj, *, is_owner, share_permission=None):
        if file_obj.deleted_at is not None:
            return False
        return is_owner
