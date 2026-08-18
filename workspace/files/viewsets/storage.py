"""Storage analysis endpoints for FileViewSet."""

from django.http import Http404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from workspace.common.uuids import parse_uuid_or_none
from workspace.files.models import File
from workspace.files.services.storage_analysis import CATEGORY_META, analyze_storage

_CATEGORY_PARAM = OpenApiParameter(
    name="category",
    type=OpenApiTypes.STR,
    enum=sorted(CATEGORY_META),
    description="Restrict the largest-files list to one file category.",
)


def _category_param(request):
    category = request.query_params.get("category") or None
    if category is not None and category not in CATEGORY_META:
        return None, Response(
            {"detail": "Unknown category."}, status=status.HTTP_400_BAD_REQUEST
        )
    return category, None


class StorageMixin:
    """Adds the account-level and per-folder ``storage`` actions."""

    @extend_schema(
        summary="Storage analysis of the personal root",
        description=(
            "Break down what takes up space in the current user's personal "
            "files: per category, per top-level folder, largest files, "
            "duplicate groups and trash."
        ),
        parameters=[_CATEGORY_PARAM],
        responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT)},
    )
    @action(detail=False, methods=["get"], url_path="storage")
    def storage_root(self, request):
        category, error = _category_param(request)
        if error is not None:
            return error
        return Response(analyze_storage(request.user, None, category=category))

    @extend_schema(
        summary="Storage analysis of a folder",
        description=(
            "Same breakdown as the root analysis, scoped to one folder's "
            "subtree. A group root folder also reports the group's trash."
        ),
        parameters=[_CATEGORY_PARAM],
        responses={
            200: OpenApiResponse(response=OpenApiTypes.OBJECT),
            400: OpenApiResponse(description="Not a folder."),
        },
    )
    @action(detail=True, methods=["get"], url_path="storage")
    def storage(self, request, uuid=None):
        folder_uuid = parse_uuid_or_none(uuid)
        if folder_uuid is None:
            raise Http404
        folder, _perm = self._resolve_file_with_access(folder_uuid)
        if folder.node_type != File.NodeType.FOLDER:
            return Response(
                {"detail": "Storage analysis applies to folders."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        category, error = _category_param(request)
        if error is not None:
            return error
        return Response(analyze_storage(request.user, folder, category=category))
