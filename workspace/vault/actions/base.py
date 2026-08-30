from abc import ABC
from enum import StrEnum

from ..models import VaultRole


class ActionCategory(StrEnum):
    EDIT = "edit"
    ORGANIZE = "organize"
    CLIPBOARD = "clipboard"
    DANGER = "danger"


# Weakest to strongest. A role missing from the map ranks below every floor,
# so one added to the model and forgotten here offers nothing, not everything.
_ROLE_RANK = {
    VaultRole.MEMBER: 1,
    VaultRole.OWNER: 2,
}


class BaseVaultAction(ABC):
    """Declarative action on a vault entry.

    ``is_available`` is pure: the resolved role, the trash flag, the type's
    field schema and the field ids the row actually carries all arrive as
    parameters, and no database query is allowed. The endpoint resolves each of those once per vault and then
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

    # A floor, compared through _ROLE_RANK. An equality against OWNER behaves
    # as one only while there are two roles: the next role sharing adds is
    # likelier to sit below MEMBER, and equality would hand it everything.
    min_role: str = VaultRole.MEMBER

    # The trash is a state an entry is in, not a place it went: an entry in it
    # keeps a different set of actions rather than none. only_when_trashed is
    # the other half, and implies the first - setting it alone would otherwise
    # declare an action offered nowhere.
    available_when_trashed: bool = False
    only_when_trashed: bool = False

    supports_bulk: bool = False
    css_class: str = ""

    def is_available(self, user, entry, *, role, trashed, schema, present_fields):
        if role is None:
            return False
        if _ROLE_RANK.get(role, 0) < _ROLE_RANK[self.min_role]:
            return False
        if trashed and not (self.available_when_trashed or self.only_when_trashed):
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
    """Hide the action unless the entry carries a given field.

    Two gates, and both are needed. The schema is the one source of what a
    type may hold, so an action that reads a field asks it rather than names
    a type: a second type that also has a TOTP field gets the action without
    this class being edited. But declaring a field and holding one are
    different things - a login entry with no authenticator key would
    otherwise be offered a copy of a code that does not exist, and the
    endpoint behind the menu item would have nothing to act on.

    The schema gate stays in front: without it, a value stored under a field
    id the type never declared could reach a reserved action.
    """

    requires_field: str

    def is_available(self, user, entry, *, role, trashed, schema, present_fields):
        if not any(field.field_id == self.requires_field for field in schema):
            return False
        if self.requires_field not in present_fields:
            return False
        return super().is_available(
            user,
            entry,
            role=role,
            trashed=trashed,
            schema=schema,
            present_fields=present_fields,
        )


class BaseVaultTargetAction(ABC):
    """Declarative action on a vault itself.

    Deliberately not a subclass of :class:`BaseVaultAction`: the state that
    decides an entry's actions - the trash, the type's schema, the fields the
    row carries - has no meaning for a vault, and inheriting a signature that
    carries all three would invite an override to read a parameter that is
    always empty.

    Every action defined against this base rewrites a field the vault's
    signed metadata covers, and that payload names the owner. There is
    therefore no floor below ``OWNER`` to express yet; ``min_role`` is
    carried anyway, so sharing adds a role rather than a branch.
    """

    id: str
    label: str
    icon: str
    category: ActionCategory

    min_role: str = VaultRole.OWNER
    supports_bulk: bool = False
    css_class: str = ""

    def is_available(self, user, vault, *, role):
        if role is None:
            return False
        return _ROLE_RANK.get(role, 0) >= _ROLE_RANK[self.min_role]

    def serialize(self, vault):
        return {
            "id": self.id,
            "label": self.label,
            "icon": self.icon,
            "category": self.category.value,
            "css_class": self.css_class,
            "bulk": self.supports_bulk,
        }
