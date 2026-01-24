import mimetypes

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from rest_framework import viewsets
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)

from .models import File
from .serializers import FileSerializer


@extend_schema_view(
    list=extend_schema(
        summary="List files and folders",
        description=(
            "Return the current user's files and folders. Supports filtering by "
            "node_type and parent, search by name or mime_type, and ordering by "
            "name, created_at, updated_at, or size. When parent is omitted, "
            "only root-level nodes are returned."
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
                description=(
                    "Filter by parent folder UUID. When omitted, only root "
                    "nodes are returned."
                ),
            ),
            OpenApiParameter(
                name="owner",
                type=OpenApiTypes.INT,
                description=(
                    "Filter by owner id. Results are always limited to the "
                    "current user."
                ),
            ),
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                description="Search in name or mime_type.",
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
                description="List of files and folders.",
            ),
        },
    ),
    retrieve=extend_schema(
        summary="Retrieve file or folder",
        description="Get a specific file or folder by UUID.",
        responses={
            200: OpenApiResponse(
                response=FileSerializer,
                description="File or folder.",
            ),
        },
    ),
    create=extend_schema(
        summary="Create file or folder",
        description=(
            "Create a file or folder. For node_type='folder', content must be "
            "empty. For node_type='file', content can be provided as "
            "multipart/form-data."
        ),
        examples=[
            OpenApiExample(
                "CreateFolder",
                summary="Create a folder",
                description="Create a folder under the root.",
                value={
                    "name": "Docs",
                    "node_type": "folder",
                    "parent": None,
                },
                request_only=True,
            ),
            OpenApiExample(
                "CreateFile",
                summary="Create a file",
                description="Create a file under a parent folder.",
                value={
                    "name": "notes.txt",
                    "node_type": "file",
                    "parent": "4f1a1b2c-3d4e-4f50-8a9b-0c1d2e3f4a5b",
                    "content": "binary",
                },
                request_only=True,
            ),
        ],
        responses={
            201: OpenApiResponse(
                response=FileSerializer,
                description="Created file or folder.",
            ),
        },
    ),
    update=extend_schema(
        summary="Replace file or folder",
        description="Full update. Fields uuid, owner, and node_type are immutable.",
        responses={
            200: OpenApiResponse(
                response=FileSerializer,
                description="Updated file or folder.",
            ),
        },
    ),
    partial_update=extend_schema(
        summary="Update file or folder",
        description="Partial update. Fields uuid, owner, and node_type are immutable.",
        responses={
            200: OpenApiResponse(
                response=FileSerializer,
                description="Updated file or folder.",
            ),
        },
    ),
    destroy=extend_schema(
        summary="Delete file or folder",
        description=(
            "Delete a file or folder. Deleting a folder cascades to its children."
        ),
        responses={
            204: OpenApiResponse(description="Deleted."),
        },
    ),
)
@extend_schema(tags=['Files'])
class FileViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing files and folders in a tree structure.

    list: Get all files/folders
    retrieve: Get a specific file/folder
    create: Create a new file/folder
    update: Update a file/folder
    destroy: Delete a file/folder (cascades to children)
    """
    serializer_class = FileSerializer
    pagination_class = None
    lookup_field = 'uuid'
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = {
        'node_type': ['exact'],
        'parent': ['exact'],
        'owner': ['exact'],
    }
    search_fields = ['name', 'mime_type']
    ordering_fields = ['name', 'created_at', 'updated_at', 'size']
    ordering = ['node_type', 'name']

    def get_queryset(self):
        """Filter by current user's files."""
        queryset = File.objects.filter(owner=self.request.user)
        if self.action == 'list' and 'parent' not in self.request.query_params:
            return queryset.filter(parent__isnull=True)
        return queryset
