from abc import ABC, abstractmethod
from enum import Enum


class ActionCategory(str, Enum):
    OPEN = 'open'
    TRANSFER = 'transfer'
    ORGANIZE = 'organize'
    EDIT = 'edit'
    INFO = 'info'
    DANGER = 'danger'
    TRASH = 'trash'


class BaseAction(ABC):
    id: str
    label: str
    icon: str
    category: ActionCategory
    node_types: tuple[str, ...]  # ('file',), ('folder',), ('file', 'folder')

    keyboard_shortcut: str | None = None
    css_class: str = ''
    supports_bulk: bool = False

    @abstractmethod
    def is_available(self, user, file_obj, *, is_owner, share_permission=None):
        """Return True if this action should appear for the given context.

        All state is passed via parameters — no DB queries allowed.
        """

    def get_label(self, file_obj):
        return self.label

    def get_icon(self, file_obj):
        return self.icon

    def get_css_class(self, file_obj):
        return self.css_class

    def serialize(self, file_obj):
        return {
            'id': self.id,
            'label': self.get_label(file_obj),
            'icon': self.get_icon(file_obj),
            'category': self.category.value,
            'shortcut': self.keyboard_shortcut,
            'css_class': self.get_css_class(file_obj),
            'bulk': self.supports_bulk,
        }
