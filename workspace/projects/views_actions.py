from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.uuids import parse_uuid_or_none

from .actions import ProjectActionRegistry
from .models import Project, Task
from .queries import get_project_role


class ProjectActionsView(APIView):
    """Bulk action availability for projects and tasks (mixed UUIDs)."""

    def post(self, request):
        uuids = request.data.get("uuids", [])
        if not isinstance(uuids, list) or not uuids:
            return Response(
                {"detail": "uuids must be a non-empty list."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(uuids) > 200:
            return Response(
                {"detail": "Too many UUIDs (max 200)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        parsed = []
        for item in uuids:
            value = parse_uuid_or_none(item)
            if value is None:
                return Response(
                    {"detail": "Malformed UUID in uuids."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            parsed.append(value)

        projects = list(Project.objects.filter(uuid__in=parsed))
        tasks = list(Task.objects.filter(uuid__in=parsed).select_related("project"))

        # One role resolution per distinct project, then pure in-memory
        # evaluation (the registry contract forbids DB queries in actions).
        role_cache = {}

        def role_for(project):
            if project.uuid not in role_cache:
                role_cache[project.uuid] = get_project_role(request.user, project)
            return role_cache[project.uuid]

        result = {}
        for project in projects:
            role = role_for(project)
            if role is None:
                continue
            result[str(project.uuid)] = ProjectActionRegistry.get_available_actions(
                request.user, project, role=role, archived=project.is_archived
            )
        for task in tasks:
            role = role_for(task.project)
            if role is None:
                continue
            result[str(task.uuid)] = ProjectActionRegistry.get_available_actions(
                request.user,
                task,
                role=role,
                archived=task.project.is_archived,
            )

        if len(result) != len(set(parsed)):
            return Response(
                {"detail": "One or more UUIDs not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(result)
