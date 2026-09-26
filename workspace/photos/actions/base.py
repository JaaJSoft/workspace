from enum import StrEnum

from workspace.common.actions import BaseAction
from workspace.photos.queries import OWNER


class ActionCategory(StrEnum):
    EDIT = "edit"
    ORGANIZE = "organize"
    DANGER = "danger"


class BaseAlbumAction(BaseAction):
    """Declarative action on an album.

    ``is_available`` is pure: the role arrives resolved (``queries.OWNER`` or
    None), no query allowed. ``roles`` lists the roles the action is offered
    to; sharing adds roles and widens the lists that fit them.
    """

    category: ActionCategory
    roles: tuple[str, ...] = (OWNER,)

    def is_available(self, user, obj, *, role):
        return role is not None and role in self.roles
