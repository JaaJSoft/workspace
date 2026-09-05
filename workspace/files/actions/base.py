from abc import abstractmethod
from enum import StrEnum

from workspace.common.actions import BaseAction


class ActionCategory(StrEnum):
    OPEN = "open"
    TRANSFER = "transfer"
    ORGANIZE = "organize"
    EDIT = "edit"
    INFO = "info"
    DANGER = "danger"
    TRASH = "trash"


class BaseFileAction(BaseAction):
    category: ActionCategory
    node_types: tuple[str, ...]  # ('file',), ('folder',), ('file', 'folder')

    keyboard_shortcut: str | None = None

    @abstractmethod
    def is_available(self, user, file_obj, *, permission):
        """Return True if this action should appear for the given context.

        ``permission`` is a :class:`~workspace.files.services.FilePermission` value.
        All state is passed via parameters - no DB queries allowed.
        """

    def serialize(self, file_obj):
        return {**super().serialize(file_obj), "shortcut": self.keyboard_shortcut}
