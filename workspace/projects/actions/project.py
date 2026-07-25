from . import ProjectActionRegistry
from .base import ActionCategory, BaseProjectAction, NotOnPersonalProjectMixin


@ProjectActionRegistry.register
class RenameProjectAction(BaseProjectAction):
    id = "rename"
    label = "Rename"
    icon = "pencil"
    category = ActionCategory.EDIT
    target_types = ("project",)
    min_role = "admin"


@ProjectActionRegistry.register
class ManageMembersAction(NotOnPersonalProjectMixin, BaseProjectAction):
    id = "manage_members"
    label = "Manage members"
    icon = "users"
    category = ActionCategory.MEMBERS
    target_types = ("project",)
    min_role = "admin"


@ProjectActionRegistry.register
class ManageLabelsAction(BaseProjectAction):
    id = "manage_labels"
    label = "Manage labels"
    icon = "tags"
    category = ActionCategory.ORGANIZE
    target_types = ("project",)
    min_role = "admin"


@ProjectActionRegistry.register
class AttachGroupAction(NotOnPersonalProjectMixin, BaseProjectAction):
    id = "attach_group"
    label = "Attach group"
    icon = "shield"
    category = ActionCategory.MEMBERS
    target_types = ("project",)
    min_role = "admin"


@ProjectActionRegistry.register
class ArchiveProjectAction(NotOnPersonalProjectMixin, BaseProjectAction):
    id = "archive"
    label = "Archive"
    icon = "archive"
    category = ActionCategory.DANGER
    target_types = ("project",)
    min_role = "admin"


@ProjectActionRegistry.register
class UnarchiveProjectAction(NotOnPersonalProjectMixin, BaseProjectAction):
    id = "unarchive"
    label = "Unarchive"
    icon = "archive-restore"
    category = ActionCategory.ORGANIZE
    target_types = ("project",)
    min_role = "admin"
    available_when_archived = True

    def is_available(self, user, obj, *, role, archived):
        if not archived:
            return False
        return super().is_available(user, obj, role=role, archived=archived)


@ProjectActionRegistry.register
class DeleteProjectAction(NotOnPersonalProjectMixin, BaseProjectAction):
    id = "delete"
    label = "Delete"
    icon = "trash-2"
    category = ActionCategory.DANGER
    target_types = ("project",)
    min_role = "admin"
    available_when_archived = True
    css_class = "text-error"
