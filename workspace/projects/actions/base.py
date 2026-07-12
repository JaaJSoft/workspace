from abc import ABC
from enum import StrEnum


class ActionCategory(StrEnum):
    EDIT = "edit"
    ORGANIZE = "organize"
    MEMBERS = "members"
    DANGER = "danger"


class BaseProjectAction(ABC):
    """Declarative action on a project or task.

    ``is_available`` is pure: all state (resolved role, archived flag)
    arrives as parameters - no DB queries allowed. Most actions only set
    the declarative attributes; special cases override ``is_available``
    and must call ``super()``. ``min_role`` values match
    ``ProjectMember.Role`` ('member' or 'admin').
    """

    id: str
    label: str
    icon: str
    category: ActionCategory
    target_types: tuple[str, ...]  # ('task',), ('project',) or both

    min_role: str = "member"
    available_when_archived: bool = False
    supports_bulk: bool = False
    css_class: str = ""

    def is_available(self, user, obj, *, role, archived):
        if role is None:
            return False
        if self.min_role == "admin" and role != "admin":
            return False
        if archived and not self.available_when_archived:
            return False
        return True

    def serialize(self, obj):
        return {
            "id": self.id,
            "label": self.label,
            "icon": self.icon,
            "category": self.category.value,
            "css_class": self.css_class,
            "bulk": self.supports_bulk,
        }


class NotOnPersonalProjectMixin:
    """Hide the action on personal projects (sharing/lifecycle actions)."""

    def is_available(self, user, obj, *, role, archived):
        if obj.type == "personal":
            return False
        return super().is_available(user, obj, role=role, archived=archived)
