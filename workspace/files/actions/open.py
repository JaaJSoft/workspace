from . import ActionRegistry
from .base import ActionCategory, BaseAction


@ActionRegistry.register
class ViewAction(BaseAction):
    id = 'view'
    label = 'Open'
    icon = 'eye'
    category = ActionCategory.OPEN
    node_types = ('file',)
    keyboard_shortcut = 'Enter / Space'

    def is_available(self, user, file_obj, *, is_owner, share_permission=None):
        if file_obj.deleted_at is not None:
            return False
        if not file_obj.is_viewable():
            return False
        return is_owner or share_permission is not None


@ActionRegistry.register
class OpenFolderAction(BaseAction):
    id = 'open'
    label = 'Open'
    icon = 'folder-open'
    category = ActionCategory.OPEN
    node_types = ('folder',)
    keyboard_shortcut = 'Enter'

    def is_available(self, user, file_obj, *, is_owner, share_permission=None):
        if file_obj.deleted_at is not None:
            return False
        return is_owner


@ActionRegistry.register
class OpenNewTabAction(BaseAction):
    id = 'open_new_tab'
    label = 'Open in new tab'
    icon = 'external-link'
    category = ActionCategory.OPEN
    node_types = ('file',)

    def is_available(self, user, file_obj, *, is_owner, share_permission=None):
        if file_obj.deleted_at is not None:
            return False
        return is_owner or share_permission is not None
