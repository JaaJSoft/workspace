from drf_spectacular.utils import extend_schema

from workspace.common.views import AvailableActionsView

from ..actions import ProjectActionRegistry
from ..models import Project, Task
from ..queries import get_project_role


@extend_schema(
    tags=["Projects"],
    summary="Get the actions available on projects and tasks",
    description="The uuids may name projects and tasks alike.",
)
class ProjectAvailableActionsView(AvailableActionsView):
    def get_available_actions(self, uuids):
        user = self.request.user
        projects = list(Project.objects.filter(uuid__in=uuids))
        tasks = list(Task.objects.filter(uuid__in=uuids).select_related("project"))

        # One role resolution per distinct project, then pure in-memory
        # evaluation (the registry contract forbids DB queries in actions).
        roles = {}

        def role_for(project):
            if project.uuid not in roles:
                roles[project.uuid] = get_project_role(user, project)
            return roles[project.uuid]

        available = {}
        for row, project in [(p, p) for p in projects] + [
            (t, t.project) for t in tasks
        ]:
            role = role_for(project)
            if role is None:
                continue
            available[row.uuid] = ProjectActionRegistry.get_available_actions(
                user, row, role=role, archived=project.is_archived
            )
        return available
