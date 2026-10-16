from workspace.files.services import FilePermission, FileService

from . import ActionRegistry
from .base import UNRESOLVED, ActionCategory, BaseFileAction


@ActionRegistry.register
class DeleteAction(BaseFileAction):
    id = "delete"
    label = "Delete"
    icon = "trash-2"
    category = ActionCategory.DANGER
    node_types = ("file", "folder")
    keyboard_shortcut = "Del"
    css_class = "text-error"
    supports_bulk = True

    def is_available(self, user, file_obj, *, permission, journal_folder=UNRESOLVED):
        if file_obj.deleted_at is not None:
            return False
        # Cannot delete a root group folder
        if FileService.is_group_root(file_obj):
            return False
        return permission is not None and permission >= FilePermission.EDIT
