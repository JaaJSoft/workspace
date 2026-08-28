"""What the caller may do with a vault, as opposed to with an entry in it.

Every action here rewrites part of ``vault-metadata``, whose signed payload
names the owner account - so each is owner-only, and a member holding a key
wrap can open the vault without being able to re-describe it.

``favorite`` and ``unfavorite`` are both declared. The registry answers what
the caller *may do*, not what the row *is*; the client shows the one matching
``is_favorite``, which it already holds.
"""

from . import VaultTargetActionRegistry
from .base import ActionCategory, BaseVaultTargetAction


@VaultTargetActionRegistry.register
class RenameVaultAction(BaseVaultTargetAction):
    id = "rename"
    label = "Rename"
    icon = "pencil"
    category = ActionCategory.EDIT


@VaultTargetActionRegistry.register
class SetVaultAppearanceAction(BaseVaultTargetAction):
    id = "set_appearance"
    label = "Icon and colour"
    icon = "palette"
    category = ActionCategory.EDIT


@VaultTargetActionRegistry.register
class FavoriteVaultAction(BaseVaultTargetAction):
    id = "favorite"
    label = "Add to favourites"
    icon = "star"
    category = ActionCategory.ORGANIZE


@VaultTargetActionRegistry.register
class UnfavoriteVaultAction(BaseVaultTargetAction):
    id = "unfavorite"
    label = "Remove from favourites"
    icon = "star-off"
    category = ActionCategory.ORGANIZE


@VaultTargetActionRegistry.register
class DeleteVaultAction(BaseVaultTargetAction):
    id = "delete"
    label = "Delete vault"
    icon = "trash-2"
    category = ActionCategory.DANGER
    css_class = "text-error"
