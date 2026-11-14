from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from . import ProjectActionRegistry
from .base import ActionCategory, BaseProjectAction


@ProjectActionRegistry.register
class EditTaskAction(BaseProjectAction):
    id = "edit"
    label = _("Edit")
    icon = "pencil"
    category = ActionCategory.EDIT
    target_types = ("task",)


@ProjectActionRegistry.register
class MoveTaskAction(BaseProjectAction):
    id = "move"
    supports_bulk = True
    label = pgettext_lazy("task action", "Move")
    icon = "move"
    category = ActionCategory.ORGANIZE
    target_types = ("task",)


@ProjectActionRegistry.register
class AssignTaskAction(BaseProjectAction):
    id = "assign"
    supports_bulk = True
    label = pgettext_lazy("task action", "Assign")
    icon = "user-plus"
    category = ActionCategory.EDIT
    target_types = ("task",)


@ProjectActionRegistry.register
class SetDueDateAction(BaseProjectAction):
    id = "set_due"
    supports_bulk = True
    label = _("Set due date")
    icon = "calendar"
    category = ActionCategory.EDIT
    target_types = ("task",)


@ProjectActionRegistry.register
class SetPriorityAction(BaseProjectAction):
    id = "set_priority"
    supports_bulk = True
    label = _("Set priority")
    icon = "flag"
    category = ActionCategory.EDIT
    target_types = ("task",)


@ProjectActionRegistry.register
class SetLabelsAction(BaseProjectAction):
    id = "set_labels"
    supports_bulk = True
    label = _("Set labels")
    icon = "tag"
    category = ActionCategory.EDIT
    target_types = ("task",)


@ProjectActionRegistry.register
class SetEpicAction(BaseProjectAction):
    id = "set_epic"
    label = _("Set epic")
    icon = "layers"
    category = ActionCategory.EDIT
    target_types = ("task",)


@ProjectActionRegistry.register
class AttachFileAction(BaseProjectAction):
    id = "attach"
    label = _("Attach files")
    icon = "paperclip"
    category = ActionCategory.EDIT
    target_types = ("task",)


@ProjectActionRegistry.register
class LinkTaskAction(BaseProjectAction):
    id = "link"
    label = pgettext_lazy("task action", "Link")
    icon = "link"
    category = ActionCategory.ORGANIZE
    target_types = ("task",)


@ProjectActionRegistry.register
class LinkFileAction(BaseProjectAction):
    id = "link_file"
    label = _("Link file")
    icon = "file-symlink"
    category = ActionCategory.ORGANIZE
    target_types = ("task",)


@ProjectActionRegistry.register
class CommentTaskAction(BaseProjectAction):
    id = "comment"
    label = pgettext_lazy("task action", "Comment")
    icon = "message-square"
    category = ActionCategory.EDIT
    target_types = ("task",)


@ProjectActionRegistry.register
class DeleteTaskAction(BaseProjectAction):
    id = "delete"
    supports_bulk = True
    label = _("Delete")
    icon = "trash-2"
    category = ActionCategory.DANGER
    target_types = ("task",)
    css_class = "text-error"
