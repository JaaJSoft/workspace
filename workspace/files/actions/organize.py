from django.utils.translation import gettext, gettext_lazy

from workspace.files.models import File
from workspace.files.services import FilePermission

from . import ActionRegistry
from .base import UNRESOLVED, ActionCategory, BaseFileAction


@ActionRegistry.register
class ToggleFavoriteAction(BaseFileAction):
    id = "toggle_favorite"
    label = gettext_lazy("Add to favorites")
    icon = "star"
    category = ActionCategory.ORGANIZE
    node_types = ("file", "folder")
    keyboard_shortcut = "F"
    supports_bulk = True

    def is_available(self, user, file_obj, *, permission, journal_folder=UNRESOLVED):
        if file_obj.deleted_at is not None:
            return False
        return permission is not None

    def get_label(self, file_obj):
        if getattr(file_obj, "is_favorite", False):
            return gettext("Remove from favorites")
        return gettext("Add to favorites")

    def get_icon(self, file_obj):
        return "star"

    def serialize(self, file_obj):
        data = super().serialize(file_obj)
        data["state"] = {"is_favorite": bool(getattr(file_obj, "is_favorite", False))}
        return data


@ActionRegistry.register
class TogglePinAction(BaseFileAction):
    id = "toggle_pin"
    label = gettext_lazy("Pin to sidebar")
    icon = "pin"
    category = ActionCategory.ORGANIZE
    node_types = ("folder",)
    keyboard_shortcut = "P"
    supports_bulk = True

    def is_available(self, user, file_obj, *, permission, journal_folder=UNRESOLVED):
        if file_obj.deleted_at is not None:
            return False
        # Group root folders are already shown in the sidebar Groups section
        if file_obj.group_id and file_obj.parent_id is None:
            return False
        return permission is not None and permission >= FilePermission.EDIT

    def get_label(self, file_obj):
        if getattr(file_obj, "is_pinned", False):
            return gettext("Unpin from sidebar")
        return gettext("Pin to sidebar")

    def get_icon(self, file_obj):
        return "pin"

    def serialize(self, file_obj):
        data = super().serialize(file_obj)
        data["state"] = {"is_pinned": bool(getattr(file_obj, "is_pinned", False))}
        return data


@ActionRegistry.register
class ToggleSensitiveAction(BaseFileAction):
    """Blur a photo for everyone who can open it, or stop blurring it.

    Reads ``sensitive_level`` (files.sensitivity.with_sensitivity): None, or
    a caller that did not annotate it, means the file cannot be marked. Any
    level above 0 offers to unmark, whatever the viewer's own preference
    blurs: the file is blurred for someone.
    """

    id = "toggle_sensitive"
    label = gettext_lazy("Mark as sensitive")
    icon = "eye-closed"
    category = ActionCategory.ORGANIZE
    node_types = ("file",)
    supports_bulk = True

    def is_available(self, user, file_obj, *, permission, journal_folder=UNRESOLVED):
        if file_obj.deleted_at is not None:
            return False
        if getattr(file_obj, "sensitive_level", None) is None:
            return False
        return permission is not None and permission >= FilePermission.EDIT

    @staticmethod
    def _is_sensitive(file_obj):
        return bool(getattr(file_obj, "sensitive_level", 0))

    def get_label(self, file_obj):
        if self._is_sensitive(file_obj):
            return gettext("Unmark as sensitive")
        return gettext("Mark as sensitive")

    def get_icon(self, file_obj):
        return "eye" if self._is_sensitive(file_obj) else "eye-closed"

    def serialize(self, file_obj):
        data = super().serialize(file_obj)
        data["state"] = {"is_sensitive": self._is_sensitive(file_obj)}
        return data


@ActionRegistry.register
class ManageTagsAction(BaseFileAction):
    id = "manage_tags"
    label = gettext_lazy("Tags")
    icon = "tag"
    category = ActionCategory.ORGANIZE
    node_types = ("file", "folder")

    def is_available(self, user, file_obj, *, permission, journal_folder=UNRESOLVED):
        if file_obj.deleted_at is not None:
            return False
        # Tags are personal: FileTagView only writes through user_files_qs,
        # which excludes group files and anything the user doesn't own.
        if file_obj.group_id:
            return False
        return permission is not None and permission >= FilePermission.MANAGE


@ActionRegistry.register
class ShareAction(BaseFileAction):
    id = "share"
    label = gettext_lazy("Share")
    icon = "share-2"
    category = ActionCategory.ORGANIZE
    node_types = ("file", "folder")

    def is_available(self, user, file_obj, *, permission, journal_folder=UNRESOLVED):
        if file_obj.deleted_at is not None:
            return False
        if permission is None or permission < FilePermission.EDIT:
            return False
        # A folder's share modal offers public links and nothing else, and those
        # are owner-only, so a non-owner editor would open a dialog that can do
        # nothing. Files still offer user-to-user sharing to an editor.
        if file_obj.node_type == File.NodeType.FOLDER:
            return file_obj.owner_id == user.id
        return True
