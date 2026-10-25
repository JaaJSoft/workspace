from django.utils.translation import gettext_lazy as _

from workspace.files.services import FilePermission

from . import ActionRegistry
from .base import UNRESOLVED, ActionCategory, BaseFileAction


@ActionRegistry.register
class ViewAction(BaseFileAction):
    id = "view"
    label = _("Open")
    icon = "eye"
    category = ActionCategory.OPEN
    node_types = ("file",)
    keyboard_shortcut = "Enter / Space"

    def is_available(self, user, file_obj, *, permission, journal_folder=UNRESOLVED):
        from workspace.files.services.scanning.policy import is_blocked

        if is_blocked(file_obj):
            return False
        if not file_obj.is_viewable():
            return False
        return permission is not None


@ActionRegistry.register
class OpenFolderAction(BaseFileAction):
    id = "open"
    label = _("Open")
    icon = "folder-open"
    category = ActionCategory.OPEN
    node_types = ("folder",)
    keyboard_shortcut = "Enter"

    def is_available(self, user, file_obj, *, permission, journal_folder=UNRESOLVED):
        if file_obj.deleted_at is not None:
            return False
        return permission is not None and permission >= FilePermission.EDIT


@ActionRegistry.register
class OpenNewTabAction(BaseFileAction):
    id = "open_new_tab"
    label = _("Open in new tab")
    icon = "external-link"
    category = ActionCategory.OPEN
    node_types = ("file",)

    def is_available(self, user, file_obj, *, permission, journal_folder=UNRESOLVED):
        from workspace.files.services.scanning.policy import is_blocked

        if file_obj.deleted_at is not None:
            return False
        if is_blocked(file_obj):
            return False
        return permission is not None
