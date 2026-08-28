from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.uuids import (
    BatchTooLarge,
    MalformedUuid,
    UuidBatchError,
    parse_uuid_batch,
)

from ..actions import ProjectActionRegistry
from ..models import Project, Task
from ..queries import get_project_role

MAX_BATCH = 200


def _refused(detail):
    return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=["Projects"],
    summary="Get available actions for projects and tasks",
    description=(
        "Return available actions for a list of project/task UUIDs. "
        "Returns a map keyed by UUID, each value being the list of "
        "available actions for that item."
    ),
    request={
        "application/json": {
            "type": "object",
            "properties": {
                "uuids": {
                    "type": "array",
                    "items": {"type": "string", "format": "uuid"},
                    "description": "List of project/task UUIDs",
                },
            },
            "required": ["uuids"],
        },
    },
    responses={
        200: OpenApiResponse(
            response=OpenApiTypes.OBJECT,
            description="Map of UUID to list of available actions.",
        ),
        400: OpenApiResponse(description="Invalid request."),
        404: OpenApiResponse(description="One or more UUIDs not found."),
    },
)
class ProjectActionsView(APIView):
    """Bulk action availability for projects and tasks (mixed UUIDs)."""

    def post(self, request):
        # The wording is chosen here from the kind of failure, never taken
        # from the exception: an exception's text is a path from the server's
        # internals to a response body.
        try:
            parsed = parse_uuid_batch(request.data, max_items=MAX_BATCH)
        except BatchTooLarge:
            return _refused(f"Too many UUIDs (max {MAX_BATCH}).")
        except MalformedUuid:
            return _refused("Malformed UUID in uuids.")
        except UuidBatchError:
            return _refused("uuids must be a non-empty list.")

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
