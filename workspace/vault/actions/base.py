from abc import ABC
from enum import StrEnum

from ..models import VaultRole


class ActionCategory(StrEnum):
    EDIT = "edit"
    ORGANIZE = "organize"
    CLIPBOARD = "clipboard"
    DANGER = "danger"


class BaseVaultAction(ABC):
    """Declarative action on a vault entry.

    ``is_available`` is pure: the resolved role, the trash flag and the
    type's field schema all arrive as parameters, and no database query is
    allowed. The endpoint resolves each of those once per vault and then
    evaluates every action in memory; an action that queried would turn one
    request into one query per entry.

    Most actions set only the attributes below. An override narrows and must
    call ``super()`` - the base class is where the role and trash rules live,
    and skipping it re-opens them.
    """

    id: str
    label: str
    icon: str
    category: ActionCategory

    # Ownership is the stronger role, so this is a floor and not an equality:
    # an action open to members stays open to the owner. Nothing but an owner
    # exists until sharing lands, and carrying the attribute now is what makes
    # sharing an addition rather than a rewrite.
    min_role: str = VaultRole.MEMBER

    # The trash is a state an entry is in, not a place it went: an entry in it
    # keeps a different set of actions rather than none. only_when_trashed is
    # the other half - restoring an entry that was never trashed is not a
    # narrower version of anything, it is meaningless.
    available_when_trashed: bool = False
    only_when_trashed: bool = False

    supports_bulk: bool = False
    css_class: str = ""

    def is_available(self, user, entry, *, role, trashed, schema):
        if role is None:
            return False
        if self.min_role == VaultRole.OWNER and role != VaultRole.OWNER:
            return False
        if trashed and not self.available_when_trashed:
            return False
        if self.only_when_trashed and not trashed:
            return False
        return True

    def serialize(self, entry):
        return {
            "id": self.id,
            "label": self.label,
            "icon": self.icon,
            "category": self.category.value,
            "css_class": self.css_class,
            "bulk": self.supports_bulk,
        }


class RequiresFieldMixin:
    """Hide the action unless the entry's type declares a given field.

    The field schema is the one source of what a type carries, so an action
    that reads a field must ask it rather than name a type: adding a second
    type that also has a TOTP field must not require editing this action.
    """

    requires_field: str

    def is_available(self, user, entry, *, role, trashed, schema):
        if not any(field.field_id == self.requires_field for field in schema):
            return False
        return super().is_available(
            user, entry, role=role, trashed=trashed, schema=schema
        )
