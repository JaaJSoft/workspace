from enum import StrEnum

from workspace.common.actions import BaseAction


class ActionCategory(StrEnum):
    EDIT = "edit"
    ORGANIZE = "organize"
    DANGER = "danger"


class BasePersonAction(BaseAction):
    """Pure availability rule: ``has_groups`` is resolved once by the caller.

    Every reachable person is editable (no roles), so most actions are
    always offered and only the ones tied to state override this.
    """

    category: ActionCategory

    def is_available(self, user, obj, *, has_groups):
        return True
