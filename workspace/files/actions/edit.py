from workspace.files.services import FilePermission, FileService
from workspace.files.services.scanning.policy import is_blocked

from . import ActionRegistry
from .base import UNRESOLVED, ActionCategory, BaseFileAction


@ActionRegistry.register
class RenameAction(BaseFileAction):
    id = "rename"
    label = "Rename"
    icon = "pencil"
    category = ActionCategory.EDIT
    node_types = ("file", "folder")
    keyboard_shortcut = "F2"

    def is_available(self, user, file_obj, *, permission, journal_folder=UNRESOLVED):
        if file_obj.deleted_at is not None:
            return False
        # Cannot rename a root group folder
        if FileService.is_group_root(file_obj):
            return False
        # Journal notes are auto-named by date; renaming would break the
        # "today's note" auto-selection logic in the notes UI.
        from workspace.notes.services.journal import is_journal_note, journal_folder_id

        if journal_folder is UNRESOLVED:
            journal_folder = journal_folder_id(user)
        if is_journal_note(user, file_obj, journal_folder):
            return False
        return permission is not None and permission >= FilePermission.EDIT


@ActionRegistry.register
class CutAction(BaseFileAction):
    id = "cut"
    label = "Cut"
    icon = "scissors"
    category = ActionCategory.EDIT
    node_types = ("file", "folder")
    keyboard_shortcut = "Ctrl+X"
    supports_bulk = True

    def is_available(self, user, file_obj, *, permission, journal_folder=UNRESOLVED):
        if file_obj.deleted_at is not None:
            return False
        # Cannot cut a root group folder (structural, one per group)
        if FileService.is_group_root(file_obj):
            return False
        return permission is not None and permission >= FilePermission.EDIT


@ActionRegistry.register
class CopyAction(BaseFileAction):
    id = "copy"
    label = "Copy"
    icon = "copy"
    category = ActionCategory.EDIT
    node_types = ("file", "folder")
    keyboard_shortcut = "Ctrl+C"
    supports_bulk = True

    def is_available(self, user, file_obj, *, permission, journal_folder=UNRESOLVED):
        if file_obj.deleted_at is not None:
            return False
        # Cannot copy a root group folder (structural, one per group)
        if FileService.is_group_root(file_obj):
            return False
        # A copy would carry the bytes out of quarantine.
        if is_blocked(file_obj):
            return False
        return permission is not None and permission >= FilePermission.EDIT


@ActionRegistry.register
class PasteIntoAction(BaseFileAction):
    id = "paste_into"
    label = "Paste here"
    icon = "clipboard-paste"
    category = ActionCategory.EDIT
    node_types = ("folder",)
    keyboard_shortcut = "Ctrl+V"

    def is_available(self, user, file_obj, *, permission, journal_folder=UNRESOLVED):
        if file_obj.deleted_at is not None:
            return False
        return permission is not None and permission >= FilePermission.EDIT
