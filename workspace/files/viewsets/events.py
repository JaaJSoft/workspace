"""Events action for FileViewSet: read-only timeline per file."""

from django.http import Http404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from workspace.files.services.events import events_for_file, serialize_event

DEFAULT_EVENTS_LIMIT = 50
MAX_EVENTS_LIMIT = 200


class EventsMixin:
    """Adds the events action: GET /api/v1/files/<uuid>/events."""

    @extend_schema(
        summary="List events for a file",
        description=(
            "Return the audit log for a file or folder, newest first. "
            "Includes operations like create, rename, move, share, "
            "delete, and restore - read operations are excluded."
        ),
        parameters=[
            OpenApiParameter(
                name="limit",
                type=OpenApiTypes.INT,
                description=(
                    f"Number of events to return (default {DEFAULT_EVENTS_LIMIT}, "
                    f"max {MAX_EVENTS_LIMIT})."
                ),
            ),
            OpenApiParameter(
                name="offset",
                type=OpenApiTypes.INT,
                description="Pagination offset (default 0).",
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="List of events plus total count.",
            ),
            404: OpenApiResponse(description="File not found."),
        },
    )
    @action(detail=True, methods=["get"], url_path="events")
    def events(self, request, uuid=None):
        """List events for a single file - shared users included."""
        try:
            file_obj, _perm = self._resolve_file_with_access(uuid)
        except Http404:
            return Response(status=status.HTTP_404_NOT_FOUND)

        try:
            limit = int(request.query_params.get("limit", DEFAULT_EVENTS_LIMIT))
        except (TypeError, ValueError):
            limit = DEFAULT_EVENTS_LIMIT
        try:
            offset = int(request.query_params.get("offset", 0))
        except (TypeError, ValueError):
            offset = 0
        limit = max(1, min(limit, MAX_EVENTS_LIMIT))
        offset = max(0, offset)

        qs = events_for_file(file_obj)
        total = qs.count()
        events = list(qs[offset : offset + limit])

        return Response(
            {
                "count": total,
                "limit": limit,
                "offset": offset,
                "results": [serialize_event(e) for e in events],
            }
        )
