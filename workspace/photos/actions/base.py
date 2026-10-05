from enum import StrEnum

from workspace.common.actions import BaseAction
from workspace.photos.queries import MANAGER, OWNER


class ActionCategory(StrEnum):
    EDIT = "edit"
    ORGANIZE = "organize"
    SHARE = "share"
    DANGER = "danger"


class BaseAlbumAction(BaseAction):
    """Declarative action on an album.

    ``is_available`` is pure: the role arrives resolved (``queries.OWNER``,
    ``MANAGER``, ``CONTRIBUTOR``, ``VIEWER`` or None), and ``direct`` says
    whether the album is shared with the user by name; no query allowed.
    ``roles`` lists the roles the action is offered to.
    """

    category: ActionCategory
    roles: tuple[str, ...] = (OWNER, MANAGER)

    def is_available(self, user, obj, *, role, direct=False):
        return role is not None and role in self.roles
