from . import ActionRegistry
from .base import ActionCategory, BaseAction


@ActionRegistry.register
class RenameAction(BaseAction):
    id = 'rename'
    label = 'Rename'
    icon = 'pencil'
    category = ActionCategory.EDIT
    node_types = ('file', 'folder')
    keyboard_shortcut = 'F2'

    def is_available(self, user, file_obj, *, is_owner, share_permission=None):
        if file_obj.deleted_at is not None:
            return False
        return is_owner


@ActionRegistry.register
class CutAction(BaseAction):
    id = 'cut'
    label = 'Cut'
    icon = 'scissors'
    category = ActionCategory.EDIT
    node_types = ('file', 'folder')
    keyboard_shortcut = 'Ctrl+X'
    supports_bulk = True

    def is_available(self, user, file_obj, *, is_owner, share_permission=None):
        if file_obj.deleted_at is not None:
            return False
        return is_owner


@ActionRegistry.register
class CopyAction(BaseAction):
    id = 'copy'
    label = 'Copy'
    icon = 'copy'
    category = ActionCategory.EDIT
    node_types = ('file', 'folder')
    keyboard_shortcut = 'Ctrl+C'
    supports_bulk = True

    def is_available(self, user, file_obj, *, is_owner, share_permission=None):
        if file_obj.deleted_at is not None:
            return False
        return is_owner


@ActionRegistry.register
class PasteIntoAction(BaseAction):
    id = 'paste_into'
    label = 'Paste here'
    icon = 'clipboard-paste'
    category = ActionCategory.EDIT
    node_types = ('folder',)
    keyboard_shortcut = 'Ctrl+V'

    def is_available(self, user, file_obj, *, is_owner, share_permission=None):
        if file_obj.deleted_at is not None:
            return False
        return is_owner
