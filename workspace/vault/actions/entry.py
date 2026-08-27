from ..models import VaultRole
from . import VaultActionRegistry
from .base import ActionCategory, BaseVaultAction, RequiresFieldMixin


@VaultActionRegistry.register
class EditEntryAction(BaseVaultAction):
    id = "edit"
    label = "Edit"
    icon = "pencil"
    category = ActionCategory.EDIT


@VaultActionRegistry.register
class CopyUsernameAction(RequiresFieldMixin, BaseVaultAction):
    id = "copy_username"
    label = "Copy username"
    icon = "user"
    category = ActionCategory.CLIPBOARD
    requires_field = "username"


@VaultActionRegistry.register
class CopyPasswordAction(RequiresFieldMixin, BaseVaultAction):
    id = "copy_password"
    label = "Copy password"
    icon = "key-round"
    category = ActionCategory.CLIPBOARD
    requires_field = "password"


@VaultActionRegistry.register
class CopyTotpAction(RequiresFieldMixin, BaseVaultAction):
    id = "copy_totp"
    label = "Copy authenticator code"
    icon = "timer"
    category = ActionCategory.CLIPBOARD
    requires_field = "totp"


@VaultActionRegistry.register
class OpenUriAction(RequiresFieldMixin, BaseVaultAction):
    id = "open_uri"
    label = "Open website"
    icon = "external-link"
    category = ActionCategory.EDIT
    requires_field = "uri"


@VaultActionRegistry.register
class MoveEntryAction(BaseVaultAction):
    id = "move"
    label = "Move to folder"
    icon = "folder"
    category = ActionCategory.ORGANIZE
    supports_bulk = True


@VaultActionRegistry.register
class SetTagsAction(BaseVaultAction):
    id = "set_tags"
    label = "Edit tags"
    icon = "tag"
    category = ActionCategory.ORGANIZE
    supports_bulk = True


@VaultActionRegistry.register
class FavoriteAction(BaseVaultAction):
    id = "favorite"
    label = "Add to favourites"
    icon = "star"
    category = ActionCategory.ORGANIZE
    supports_bulk = True


@VaultActionRegistry.register
class UnfavoriteAction(BaseVaultAction):
    id = "unfavorite"
    label = "Remove from favourites"
    icon = "star-off"
    category = ActionCategory.ORGANIZE
    supports_bulk = True


@VaultActionRegistry.register
class TrashEntryAction(BaseVaultAction):
    id = "trash"
    label = "Move to trash"
    icon = "trash-2"
    category = ActionCategory.DANGER
    css_class = "text-error"
    supports_bulk = True


@VaultActionRegistry.register
class RestoreEntryAction(BaseVaultAction):
    id = "restore"
    label = "Restore"
    icon = "undo-2"
    category = ActionCategory.ORGANIZE
    available_when_trashed = True
    only_when_trashed = True
    supports_bulk = True


@VaultActionRegistry.register
class DeleteEntryForeverAction(BaseVaultAction):
    id = "delete_forever"
    label = "Delete for good"
    icon = "trash-2"
    category = ActionCategory.DANGER
    css_class = "text-error"
    min_role = VaultRole.OWNER
    available_when_trashed = True
    only_when_trashed = True
    supports_bulk = True
