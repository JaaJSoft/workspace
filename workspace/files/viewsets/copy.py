"""Copy action for FileViewSet."""

from django.core.exceptions import ValidationError
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from workspace.files.models import File
from workspace.files.serializers import FileSerializer
from workspace.files.services import FileService


class CopyMixin:
    """Adds the copy action."""

    @extend_schema(
        summary="Copy file or folder",
        description=(
            "Create a copy of a file or folder. For folders, copies recursively. "
            "The copy is placed in the specified parent folder (or root if not specified)."
        ),
        request={
            'application/json': {
                'type': 'object',
                'properties': {
                    'parent': {
                        'type': 'string',
                        'format': 'uuid',
                        'nullable': True,
                        'description': 'Target parent folder UUID (null for root)',
                    },
                },
            },
        },
        responses={
            201: OpenApiResponse(
                response=FileSerializer,
                description="Copied file or folder.",
            ),
            400: OpenApiResponse(description="Invalid request."),
        },
    )
    @action(detail=True, methods=['post'], url_path='copy')
    def copy(self, request, uuid=None):
        """Copy a file or folder to a new location."""
        file_obj = self.get_object()
        parent_uuid = request.data.get('parent')

        # Resolve parent folder
        parent = None
        if parent_uuid:
            try:
                parent = File.objects.get(
                    uuid=parent_uuid,
                    owner=request.user,
                    node_type=File.NodeType.FOLDER,
                    deleted_at__isnull=True,
                )
            except (File.DoesNotExist, ValidationError, ValueError):
                # ValidationError covers malformed UUID strings; ValueError
                # catches the legacy raise paths. Without these, ?parent=foo
                # returns 500 instead of a clean 400.
                return Response(
                    {'detail': 'Parent folder not found.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Cannot copy into itself or its descendants. `path` is denormalized
        # but may be None for newly created folders, so fall back to
        # get_path() to avoid AttributeError on .startswith.
        if file_obj.node_type == File.NodeType.FOLDER and parent:
            file_path = file_obj.path or file_obj.get_path()
            parent_path = parent.path or parent.get_path()
            if parent.uuid == file_obj.uuid or parent_path.startswith(f"{file_path}/"):
                return Response(
                    {'detail': 'Cannot copy folder into itself or its descendants.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Perform the copy
        copied = FileService.copy(file_obj, parent, request.user)
        # Re-fetch through get_queryset() so the instance carries the
        # annotations FileSerializer now requires.
        annotated = self.get_queryset().filter(pk=copied.pk).first() or copied
        serializer = self.get_serializer(annotated)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
