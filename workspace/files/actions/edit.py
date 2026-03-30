from workspace.files.services import FilePermission
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

    def is_available(self, user, file_obj, *, permission):
        if file_obj.deleted_at is not None:
            return False
        # Cannot rename a root group folder
        if file_obj.group_id and file_obj.parent_id is None:
            return False
        return permission is not None and permission >= FilePermission.EDIT


@ActionRegistry.register
class CutAction(BaseAction):
    id = 'cut'
    label = 'Cut'
    icon = 'scissors'
    category = ActionCategory.EDIT
    node_types = ('file', 'folder')
    keyboard_shortcut = 'Ctrl+X'
    supports_bulk = True

    def is_available(self, user, file_obj, *, permission):
        if file_obj.deleted_at is not None:
            return False
        # Cannot cut a root group folder (structural, one per group)
        if file_obj.group_id and file_obj.parent_id is None:
            return False
        return permission is not None and permission >= FilePermission.EDIT


@ActionRegistry.register
class CopyAction(BaseAction):
    id = 'copy'
    label = 'Copy'
    icon = 'copy'
    category = ActionCategory.EDIT
    node_types = ('file', 'folder')
    keyboard_shortcut = 'Ctrl+C'
    supports_bulk = True

    def is_available(self, user, file_obj, *, permission):
        if file_obj.deleted_at is not None:
            return False
        # Cannot copy a root group folder (structural, one per group)
        if file_obj.group_id and file_obj.parent_id is None:
            return False
        return permission is not None and permission >= FilePermission.EDIT


@ActionRegistry.register
class PasteIntoAction(BaseAction):
    id = 'paste_into'
    label = 'Paste here'
    icon = 'clipboard-paste'
    category = ActionCategory.EDIT
    node_types = ('folder',)
    keyboard_shortcut = 'Ctrl+V'

    def is_available(self, user, file_obj, *, permission):
        if file_obj.deleted_at is not None:
            return False
        return permission is not None and permission >= FilePermission.EDIT
