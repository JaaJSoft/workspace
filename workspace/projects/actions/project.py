from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from . import ProjectActionRegistry
from .base import ActionCategory, BaseProjectAction, NotOnPersonalProjectMixin


@ProjectActionRegistry.register
class RenameProjectAction(BaseProjectAction):
    id = "rename"
    label = _("Rename")
    icon = "pencil"
    category = ActionCategory.EDIT
    target_types = ("project",)
    min_role = "admin"


@ProjectActionRegistry.register
class ManageMembersAction(NotOnPersonalProjectMixin, BaseProjectAction):
    id = "manage_members"
    label = _("Manage members")
    icon = "users"
    category = ActionCategory.MEMBERS
    target_types = ("project",)
    min_role = "admin"


@ProjectActionRegistry.register
class ManageLabelsAction(BaseProjectAction):
    id = "manage_labels"
    label = _("Manage labels")
    icon = "tags"
    category = ActionCategory.ORGANIZE
    target_types = ("project",)
    min_role = "admin"


@ProjectActionRegistry.register
class ManageEpicsAction(BaseProjectAction):
    id = "manage_epics"
    label = _("Manage epics")
    icon = "layers"
    category = ActionCategory.ORGANIZE
    target_types = ("project",)
    min_role = "admin"


@ProjectActionRegistry.register
class ManageSprintsAction(BaseProjectAction):
    id = "manage_sprints"
    label = _("Manage sprints")
    icon = "timer"
    category = ActionCategory.ORGANIZE
    target_types = ("project",)
    min_role = "admin"

    def is_available(self, user, obj, *, role, archived):
        # Sprints only exist on scrum projects.
        if obj.type != "scrum":
            return False
        return super().is_available(user, obj, role=role, archived=archived)


@ProjectActionRegistry.register
class AttachGroupAction(NotOnPersonalProjectMixin, BaseProjectAction):
    id = "attach_group"
    label = _("Manage groups")
    icon = "shield"
    category = ActionCategory.MEMBERS
    target_types = ("project",)
    min_role = "admin"


@ProjectActionRegistry.register
class ArchiveProjectAction(NotOnPersonalProjectMixin, BaseProjectAction):
    id = "archive"
    label = pgettext_lazy("project action", "Archive")
    icon = "archive"
    category = ActionCategory.DANGER
    target_types = ("project",)
    min_role = "admin"


@ProjectActionRegistry.register
class UnarchiveProjectAction(NotOnPersonalProjectMixin, BaseProjectAction):
    id = "unarchive"
    label = pgettext_lazy("project action", "Unarchive")
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
    label = _("Delete")
    icon = "trash-2"
    category = ActionCategory.DANGER
    target_types = ("project",)
    min_role = "admin"
    available_when_archived = True
    css_class = "text-error"
