from django.utils.translation import gettext_lazy as _

from ..models import VaultRole
from . import VaultActionRegistry
from .base import ActionCategory, BaseVaultAction, RequiresFieldMixin


@VaultActionRegistry.register
class EditEntryAction(BaseVaultAction):
    id = "edit"
    label = _("Edit")
    icon = "pencil"
    category = ActionCategory.EDIT


@VaultActionRegistry.register
class CopyUsernameAction(RequiresFieldMixin, BaseVaultAction):
    id = "copy_username"
    label = _("Copy username")
    icon = "user"
    category = ActionCategory.CLIPBOARD
    requires_field = "username"


@VaultActionRegistry.register
class CopyPasswordAction(RequiresFieldMixin, BaseVaultAction):
    id = "copy_password"
    label = _("Copy password")
    icon = "key-round"
    category = ActionCategory.CLIPBOARD
    requires_field = "password"


@VaultActionRegistry.register
class CopyTotpAction(RequiresFieldMixin, BaseVaultAction):
    id = "copy_totp"
    label = _("Copy authenticator code")
    icon = "timer"
    category = ActionCategory.CLIPBOARD
    requires_field = "totp"


@VaultActionRegistry.register
class OpenUriAction(RequiresFieldMixin, BaseVaultAction):
    id = "open_uri"
    label = _("Open website")
    icon = "external-link"
    category = ActionCategory.EDIT
    requires_field = "uri"


@VaultActionRegistry.register
class MoveEntryAction(BaseVaultAction):
    id = "move"
    label = _("Move to folder")
    icon = "folder"
    category = ActionCategory.ORGANIZE
    supports_bulk = True


@VaultActionRegistry.register
class SetTagsAction(BaseVaultAction):
    id = "set_tags"
    label = _("Edit tags")
    icon = "tag"
    category = ActionCategory.ORGANIZE
    supports_bulk = True


@VaultActionRegistry.register
class FavoriteAction(BaseVaultAction):
    id = "favorite"
    label = _("Add to favourites")
    icon = "star"
    category = ActionCategory.ORGANIZE
    supports_bulk = True


@VaultActionRegistry.register
class UnfavoriteAction(BaseVaultAction):
    id = "unfavorite"
    label = _("Remove from favourites")
    icon = "star-off"
    category = ActionCategory.ORGANIZE
    supports_bulk = True


@VaultActionRegistry.register
class TrashEntryAction(BaseVaultAction):
    id = "trash"
    label = _("Move to trash")
    icon = "trash-2"
    category = ActionCategory.DANGER
    css_class = "text-error"
    supports_bulk = True


@VaultActionRegistry.register
class RestoreEntryAction(BaseVaultAction):
    id = "restore"
    label = _("Restore")
    icon = "undo-2"
    category = ActionCategory.ORGANIZE
    only_when_trashed = True
    supports_bulk = True


@VaultActionRegistry.register
class DeleteEntryForeverAction(BaseVaultAction):
    id = "delete_forever"
    label = _("Delete permanently")
    icon = "trash-2"
    category = ActionCategory.DANGER
    css_class = "text-error"
    min_role = VaultRole.OWNER
    only_when_trashed = True
    supports_bulk = True
