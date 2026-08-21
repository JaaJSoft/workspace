from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.uuids import parse_uuid_or_none

from .services.search import reference_tasks_qs, search_tasks_qs

MAX_RESULTS = 10


@extend_schema(
    tags=["Projects"],
    summary="Search tasks across accessible projects",
    description=(
        "Compact task lookup for pickers (the task-link picker). Exact "
        "reference matches (WR-42, #42, 42) come first, then full-text "
        "matches on title and description. Archived projects are excluded."
    ),
    parameters=[
        OpenApiParameter(
            name="q",
            type=str,
            required=True,
            description="Reference (WR-42, #42, 42) or free text.",
        ),
        OpenApiParameter(
            name="exclude",
            type=OpenApiTypes.UUID,
            description="Task UUID to omit from the results (the picker's anchor).",
        ),
    ],
    responses={
        200: OpenApiResponse(
            response=OpenApiTypes.OBJECT,
            description="Up to 10 matches, best first.",
        ),
    },
)
class TaskSearchView(APIView):
    def get(self, request):
        query = (request.query_params.get("q") or "").strip()
        exclude = None
        exclude_raw = request.query_params.get("exclude")
        if exclude_raw:
            exclude = parse_uuid_or_none(exclude_raw)
            if exclude is None:
                return Response(
                    {"detail": "Malformed exclude UUID."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        if not query:
            return Response([])
        results = []
        seen = set()
        for qs in (
            reference_tasks_qs(request.user, query),
            search_tasks_qs(request.user, query),
        ):
            if len(results) >= MAX_RESULTS:
                break
            qs = qs.select_related("project")
            if exclude is not None:
                qs = qs.exclude(uuid=exclude)
            for task in qs[:MAX_RESULTS]:
                if task.uuid in seen:
                    continue
                seen.add(task.uuid)
                results.append(
                    {
                        "uuid": str(task.uuid),
                        "reference": f"{task.project.key}-{task.number}",
                        "title": task.title,
                        "project_name": task.project.name,
                    }
                )
        return Response(results[:MAX_RESULTS])
