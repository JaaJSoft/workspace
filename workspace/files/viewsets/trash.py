"""Trash listing for FileViewSet.

Restoring and purging go through batch operations.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework.decorators import action
from rest_framework.response import Response

from workspace.files.models import File
from workspace.files.queries import annotate_for_serializer, restorable_files_qs
from workspace.files.serializers import FileSerializer


class TrashMixin:
    """Adds the trash listing."""

    @extend_schema(
        summary="List trashed files and folders",
        description=(
            "Return the trashed items the current user may restore: their "
            "own, and those of the groups they belong to."
        ),
        parameters=[
            OpenApiParameter(
                name="node_type",
                type=OpenApiTypes.STR,
                enum=[File.NodeType.FILE, File.NodeType.FOLDER],
                description="Filter by node type.",
            ),
            OpenApiParameter(
                name="parent",
                type=OpenApiTypes.UUID,
                description="Filter by parent folder UUID.",
            ),
            OpenApiParameter(
                name="owner",
                type=OpenApiTypes.INT,
                description=(
                    "Filter by owner id. Results are always limited to what "
                    "the current user may restore."
                ),
            ),
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                description=(
                    "Full-text search over file names and, for text "
                    "formats, file contents."
                ),
            ),
            OpenApiParameter(
                name="ordering",
                type=OpenApiTypes.STR,
                description=(
                    "Comma-separated fields. Use '-' for descending. Allowed: "
                    "name, created_at, updated_at, size."
                ),
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=FileSerializer(many=True),
                description="List of trashed files and folders.",
            ),
        },
    )
    @action(detail=False, methods=["get"], url_path="trash")
    def trash(self, request):
        """List trashed files and folders."""
        queryset = restorable_files_qs(request.user).filter(deleted_at__isnull=False)
        queryset = annotate_for_serializer(queryset, request.user)
        queryset = self.filter_queryset(queryset)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
