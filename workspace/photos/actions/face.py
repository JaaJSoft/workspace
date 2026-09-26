from workspace.common.actions import BaseAction
from workspace.photos.models import Face

from . import FaceActionRegistry
from .base import ActionCategory


class BaseFaceAction(BaseAction):
    """Declarative correction of one of the user's faces; every one applies
    to a selection too."""

    category = ActionCategory.ORGANIZE
    supports_bulk = True

    def is_available(self, user, obj):
        return True


@FaceActionRegistry.register
class ConfirmFaceAction(BaseFaceAction):
    id = "confirm"
    label = "That's right"
    icon = "check"

    def is_available(self, user, obj):
        return obj.cluster_id is not None and obj.assignment == Face.Assignment.AUTO


@FaceActionRegistry.register
class AssignFaceAction(BaseFaceAction):
    id = "assign"
    label = "This is..."
    icon = "user-round-pen"


@FaceActionRegistry.register
class RejectFaceAction(BaseFaceAction):
    id = "reject"
    label = "Not this person"
    icon = "user-x"
    css_class = "text-error"

    def is_available(self, user, obj):
        return obj.cluster_id is not None


@FaceActionRegistry.register
class HideFaceAction(BaseFaceAction):
    id = "hide"
    label = "Hide"
    icon = "eye-off"

    def is_available(self, user, obj):
        return obj.assignment != Face.Assignment.HIDDEN


@FaceActionRegistry.register
class UnhideFaceAction(BaseFaceAction):
    id = "unhide"
    label = "Unhide"
    icon = "eye"

    def is_available(self, user, obj):
        return obj.assignment == Face.Assignment.HIDDEN
