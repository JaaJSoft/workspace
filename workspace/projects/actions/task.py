from . import ProjectActionRegistry
from .base import ActionCategory, BaseProjectAction


@ProjectActionRegistry.register
class EditTaskAction(BaseProjectAction):
    id = "edit"
    label = "Edit"
    icon = "pencil"
    category = ActionCategory.EDIT
    target_types = ("task",)


@ProjectActionRegistry.register
class MoveTaskAction(BaseProjectAction):
    id = "move"
    label = "Move"
    icon = "move"
    category = ActionCategory.ORGANIZE
    target_types = ("task",)


@ProjectActionRegistry.register
class AssignTaskAction(BaseProjectAction):
    id = "assign"
    label = "Assign"
    icon = "user-plus"
    category = ActionCategory.EDIT
    target_types = ("task",)


@ProjectActionRegistry.register
class SetDueDateAction(BaseProjectAction):
    id = "set_due"
    label = "Set due date"
    icon = "calendar"
    category = ActionCategory.EDIT
    target_types = ("task",)


@ProjectActionRegistry.register
class SetLabelsAction(BaseProjectAction):
    id = "set_labels"
    label = "Set labels"
    icon = "tag"
    category = ActionCategory.EDIT
    target_types = ("task",)


@ProjectActionRegistry.register
class SetEpicAction(BaseProjectAction):
    id = "set_epic"
    label = "Set epic"
    icon = "layers"
    category = ActionCategory.EDIT
    target_types = ("task",)


@ProjectActionRegistry.register
class AttachFileAction(BaseProjectAction):
    id = "attach"
    label = "Attach files"
    icon = "paperclip"
    category = ActionCategory.EDIT
    target_types = ("task",)


@ProjectActionRegistry.register
class LinkTaskAction(BaseProjectAction):
    id = "link"
    label = "Link"
    icon = "link"
    category = ActionCategory.ORGANIZE
    target_types = ("task",)


@ProjectActionRegistry.register
class CommentTaskAction(BaseProjectAction):
    id = "comment"
    label = "Comment"
    icon = "message-square"
    category = ActionCategory.EDIT
    target_types = ("task",)


@ProjectActionRegistry.register
class DeleteTaskAction(BaseProjectAction):
    id = "delete"
    label = "Delete"
    icon = "trash-2"
    category = ActionCategory.DANGER
    target_types = ("task",)
    css_class = "text-error"
