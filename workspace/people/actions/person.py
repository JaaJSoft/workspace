from . import PersonActionRegistry
from .base import ActionCategory, BasePersonAction


@PersonActionRegistry.register
class EditAction(BasePersonAction):
    id = "edit"
    label = "Edit"
    icon = "pencil"
    category = ActionCategory.EDIT


@PersonActionRegistry.register
class UnlinkUserAction(BasePersonAction):
    id = "unlink_user"
    label = "Unlink account"
    icon = "unlink"
    category = ActionCategory.EDIT

    def is_available(self, user, obj, *, has_groups):
        return obj.linked_user_id is not None


@PersonActionRegistry.register
class AddToListAction(BasePersonAction):
    id = "add_to_list"
    label = "Add to list"
    icon = "list-plus"
    category = ActionCategory.ORGANIZE
    supports_bulk = True


@PersonActionRegistry.register
class MoveAction(BasePersonAction):
    id = "move"
    label = "Move to..."
    icon = "users"
    category = ActionCategory.ORGANIZE
    supports_bulk = True

    def is_available(self, user, obj, *, has_groups):
        return has_groups


@PersonActionRegistry.register
class ExportAction(BasePersonAction):
    id = "export"
    label = "Export vCard"
    icon = "download"
    category = ActionCategory.ORGANIZE


@PersonActionRegistry.register
class QRCodeAction(BasePersonAction):
    id = "qrcode"
    label = "Show QR code"
    icon = "qr-code"
    category = ActionCategory.ORGANIZE


@PersonActionRegistry.register
class DeleteAction(BasePersonAction):
    id = "delete"
    label = "Delete"
    icon = "trash-2"
    category = ActionCategory.DANGER
    css_class = "text-error"
    supports_bulk = True
