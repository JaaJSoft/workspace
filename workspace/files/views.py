import logging

from django.conf import settings
from django.db.models import Exists, OuterRef
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from datetime import timedelta
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)

logger = logging.getLogger(__name__)

from .models import File, FileFavorite, PinnedFolder
from .serializers import FileSerializer
from workspace.files.services import FileService

RECENT_FILES_LIMIT = getattr(settings, 'RECENT_FILES_LIMIT', 25)
RECENT_FILES_MAX_LIMIT = getattr(settings, 'RECENT_FILES_MAX_LIMIT', 200)
TRASH_RETENTION_DAYS = getattr(settings, 'TRASH_RETENTION_DAYS', 30)


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
                name="favorites",
                type=OpenApiTypes.BOOL,
                description="When true, return only favorited items.",
            ),
            OpenApiParameter(
                name="recent",
                type=OpenApiTypes.BOOL,
                description=(
                    "When true, return recently updated items ordered by "
                    "updated_at desc."
                ),
            ),
            OpenApiParameter(
                name="trashed",
                type=OpenApiTypes.BOOL,
                description="When true, return only items in trash.",
            ),
            OpenApiParameter(
                name="recent_limit",
                type=OpenApiTypes.INT,
                description=(
                    "Limit the number of recent items returned. Defaults to "
                    f"{RECENT_FILES_LIMIT} and capped at {RECENT_FILES_MAX_LIMIT}."
                ),
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

    def _is_favorites_query(self):
        value = self.request.query_params.get('favorites')
        if value is None:
            return False
        return str(value).lower() in {'1', 'true', 'yes'}

    def _is_recent_query(self):
        value = self.request.query_params.get('recent')
        if value is None:
            return False
        return str(value).lower() in {'1', 'true', 'yes'}

    def _is_trash_query(self):
        value = self.request.query_params.get('trashed')
        if value is None:
            return False
        return str(value).lower() in {'1', 'true', 'yes'}

    def _get_recent_limit(self):
        value = self.request.query_params.get('recent_limit')
        if value is None:
            return RECENT_FILES_LIMIT
        try:
            limit = int(value)
        except (TypeError, ValueError):
            return RECENT_FILES_LIMIT
        if limit <= 0:
            return RECENT_FILES_LIMIT
        return min(limit, RECENT_FILES_MAX_LIMIT)

    def get_queryset(self):
        """Filter by current user's files."""
        queryset = File.objects.filter(owner=self.request.user)
        favorite_subquery = FileFavorite.objects.filter(
            owner=self.request.user,
            file_id=OuterRef('pk'),
        )
        pinned_subquery = PinnedFolder.objects.filter(
            owner=self.request.user,
            folder_id=OuterRef('pk'),
        )
        queryset = queryset.annotate(
            is_favorite=Exists(favorite_subquery),
            is_pinned=Exists(pinned_subquery),
        )
        if self.action in {'trash'} or self._is_trash_query():
            return queryset.filter(deleted_at__isnull=False)
        if self.action in {'restore', 'purge'}:
            return queryset
        queryset = queryset.filter(deleted_at__isnull=True)
        if self.action == 'list':
            if self._is_favorites_query():
                return queryset.filter(favorites__owner=self.request.user).distinct()
            if self._is_trash_query():
                return queryset
            if not self._is_recent_query() and 'parent' not in self.request.query_params:
                return queryset.filter(parent__isnull=True)
        return queryset

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        if self._is_recent_query():
            queryset = queryset.order_by('-updated_at')
            limit = self._get_recent_limit()
            if limit:
                queryset = queryset[:limit]

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post', 'delete'], url_path='favorite')
    def favorite(self, request, uuid=None):
        """Add or remove a file/folder from favorites."""
        file_obj = self.get_object()
        if request.method == 'POST':
            FileFavorite.objects.get_or_create(owner=request.user, file=file_obj)
            return Response({'is_favorite': True}, status=status.HTTP_200_OK)
        FileFavorite.objects.filter(owner=request.user, file=file_obj).delete()
        return Response({'is_favorite': False}, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Pin or unpin a folder from sidebar",
        description="POST to pin, DELETE to unpin a folder from the sidebar.",
        responses={
            200: OpenApiResponse(description="Pin status updated."),
            400: OpenApiResponse(description="Not a folder."),
        },
    )
    @action(detail=True, methods=['post', 'delete'], url_path='pin')
    def pin(self, request, uuid=None):
        """Pin or unpin a folder from the sidebar."""
        file_obj = self.get_object()
        if file_obj.node_type != File.NodeType.FOLDER:
            return Response({'detail': 'Only folders can be pinned.'}, status=status.HTTP_400_BAD_REQUEST)
        if request.method == 'POST':
            max_pos = PinnedFolder.objects.filter(owner=request.user).order_by('-position').values_list('position', flat=True).first()
            position = (max_pos or 0) + 1
            PinnedFolder.objects.get_or_create(owner=request.user, folder=file_obj, defaults={'position': position})
            return Response({'is_pinned': True}, status=status.HTTP_200_OK)
        PinnedFolder.objects.filter(owner=request.user, folder=file_obj).delete()
        return Response({'is_pinned': False}, status=status.HTTP_200_OK)

    @extend_schema(
        summary="List pinned folders",
        description="Return the current user's pinned folders for the sidebar.",
        responses={
            200: OpenApiResponse(
                response=FileSerializer(many=True),
                description="List of pinned folders.",
            ),
        },
    )
    @action(detail=False, methods=['get'], url_path='pinned')
    def pinned(self, request):
        """List pinned folders."""
        pinned_qs = PinnedFolder.objects.filter(
            owner=request.user,
            folder__deleted_at__isnull=True,
        ).select_related('folder').order_by('position', 'created_at')
        folder_ids = [p.folder_id for p in pinned_qs]
        queryset = self.get_queryset().filter(pk__in=folder_ids, deleted_at__isnull=True)
        # Preserve pin order
        order_map = {fid: i for i, fid in enumerate(folder_ids)}
        folders = sorted(queryset, key=lambda f: order_map.get(f.pk, 0))
        serializer = self.get_serializer(folders, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Reorder pinned folders",
        description="Update the order of pinned folders. Send an array of folder UUIDs in the desired order.",
        request={
            'application/json': {
                'type': 'object',
                'properties': {
                    'order': {
                        'type': 'array',
                        'items': {'type': 'string', 'format': 'uuid'},
                        'description': 'Array of folder UUIDs in the desired order',
                    },
                },
                'required': ['order'],
            },
        },
        responses={
            200: OpenApiResponse(description="Order updated successfully."),
            400: OpenApiResponse(description="Invalid request."),
        },
    )
    @action(detail=False, methods=['post'], url_path='pinned/reorder')
    def pinned_reorder(self, request):
        """Reorder pinned folders."""
        order = request.data.get('order', [])
        if not isinstance(order, list):
            return Response({'detail': 'order must be a list of UUIDs.'}, status=status.HTTP_400_BAD_REQUEST)

        # Get existing pinned folders for user
        pinned_map = {
            str(p.folder_id): p
            for p in PinnedFolder.objects.filter(owner=request.user)
        }

        # Update positions based on new order
        for position, uuid in enumerate(order):
            if uuid in pinned_map:
                pin = pinned_map[uuid]
                if pin.position != position:
                    pin.position = position
                    pin.save(update_fields=['position'])

        return Response({'success': True}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='restore')
    def restore(self, request, uuid=None):
        """Restore a file or folder from trash."""
        file_obj = self.get_object()
        if file_obj.deleted_at is None:
            return Response({'detail': 'Item is not in trash.'}, status=status.HTTP_400_BAD_REQUEST)
        restored = file_obj.restore()
        return Response({'restored': restored}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['delete'], url_path='purge')
    def purge(self, request, uuid=None):
        """Permanently delete a file or folder."""
        file_obj = self.get_object()
        if file_obj.deleted_at is None:
            return Response({'detail': 'Item is not in trash.'}, status=status.HTTP_400_BAD_REQUEST)
        file_obj.delete(hard=True)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        summary="List trashed files and folders",
        description="Return the current user's trashed items.",
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
                description="List of trashed files and folders.",
            ),
        },
    )
    @action(detail=False, methods=['get'], url_path='trash')
    def trash(self, request):
        """List trashed files and folders."""
        queryset = File.objects.filter(owner=request.user, deleted_at__isnull=False)
        favorite_subquery = FileFavorite.objects.filter(
            owner=request.user,
            file_id=OuterRef('pk'),
        )
        queryset = queryset.annotate(is_favorite=Exists(favorite_subquery))
        queryset = self.filter_queryset(queryset)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Clean trash",
        description=(
            "Permanently delete trashed items past retention. "
            "Use force=true to delete all trashed items."
        ),
        parameters=[
            OpenApiParameter(
                name="force",
                type=OpenApiTypes.BOOL,
                description="When true, delete all trashed items.",
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Deletion summary.",
            ),
        },
    )
    @action(detail=False, methods=['delete'], url_path='trash/clean')
    def clean_trash(self, request):
        """Permanently delete trashed items past retention (or force all)."""
        force_value = self.request.query_params.get('force')
        force = str(force_value).lower() in {'1', 'true', 'yes'} if force_value is not None else False
        retention_days = TRASH_RETENTION_DAYS
        cutoff = timezone.now() - timedelta(days=retention_days)
        queryset = File.objects.filter(owner=request.user, deleted_at__isnull=False)
        if not force:
            queryset = queryset.filter(deleted_at__lt=cutoff)
        file_count = queryset.count()
        queryset.delete()
        return Response({
            'deleted': file_count,
            'retention_days': retention_days,
            'force': force,
        }, status=status.HTTP_200_OK)

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
            except File.DoesNotExist:
                return Response(
                    {'detail': 'Parent folder not found.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Cannot copy into itself or its descendants
        if file_obj.node_type == File.NodeType.FOLDER and parent:
            if parent.uuid == file_obj.uuid or parent.path.startswith(f"{file_obj.path}/"):
                return Response(
                    {'detail': 'Cannot copy folder into itself or its descendants.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Perform the copy
        copied = FileService.copy(file_obj, parent, request.user)
        serializer = self.get_serializer(copied)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Get file content",
        description="Serve file content with proper headers for inline viewing in browser.",
        responses={
            200: OpenApiResponse(
                description="File content with appropriate Content-Type and Content-Disposition headers.",
            ),
            400: OpenApiResponse(description="Not a file (folder)."),
            404: OpenApiResponse(description="File not found or no content."),
        },
    )
    @action(detail=True, methods=['get'], url_path='content')
    def content(self, request, uuid=None):
        """Serve file content with proper headers for inline viewing."""
        from django.http import FileResponse, HttpResponse

        file_obj = self.get_object()

        # Security: ownership check
        if file_obj.owner != request.user:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Only serve files, not folders
        if file_obj.node_type != File.NodeType.FILE:
            return Response({'detail': 'Not a file.'}, status=status.HTTP_400_BAD_REQUEST)

        # File must have content
        if not file_obj.content:
            return Response({'detail': 'No content.'}, status=status.HTTP_404_NOT_FOUND)

        # For text files, read and return directly (fixes streaming issues)
        if file_obj.mime_type and file_obj.mime_type.startswith('text/'):
            file_handle = None
            try:
                file_handle = file_obj.content.open('rb')
                content = file_handle.read().decode('utf-8')
                response = HttpResponse(content, content_type=file_obj.mime_type)
                response['Content-Disposition'] = f'inline; filename="{file_obj.name}"'
                return response
            except Exception:
                # Fallback to binary if UTF-8 fails
                pass
            finally:
                if file_handle:
                    file_handle.close()

        # For other files, use FileResponse with proper streaming
        # FileResponse will close the file handle when done
        file_handle = file_obj.content.open('rb')
        response = FileResponse(
            file_handle,
            content_type=file_obj.mime_type or 'application/octet-stream',
            as_attachment=False
        )
        response['Content-Disposition'] = f'inline; filename="{file_obj.name}"'

        return response

    @extend_schema(
        summary="Sync root folder with disk",
        description=(
            "Synchronize root-level files between disk storage and database for the "
            "current user. Adds files present on disk but missing in DB, and "
            "soft-deletes DB entries whose files no longer exist on disk."
        ),
        responses={
            200: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Sync result summary.",
            ),
        },
    )
    @action(detail=False, methods=['post'], url_path='sync')
    def sync_root(self, request):
        """Sync root-level files for the current user."""
        from workspace.files.sync import FileSyncService

        service = FileSyncService(log=logger)
        result = service.sync_folder_shallow(request.user, parent_db=None)
        return Response({
            'files_created': result.files_created,
            'folders_created': result.folders_created,
            'files_soft_deleted': result.files_soft_deleted,
            'folders_soft_deleted': result.folders_soft_deleted,
            'errors': result.errors,
        })

    @extend_schema(
        summary="Sync folder with disk",
        description=(
            "Synchronize a specific folder's immediate children between disk storage "
            "and database. Adds files present on disk but missing in DB, and "
            "soft-deletes DB entries whose files no longer exist on disk."
        ),
        responses={
            200: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Sync result summary.",
            ),
            400: OpenApiResponse(description="Not a folder."),
        },
    )
    @action(detail=True, methods=['post'], url_path='sync')
    def sync_folder(self, request, uuid=None):
        """Sync a specific folder's children for the current user."""
        from workspace.files.sync import FileSyncService

        file_obj = self.get_object()
        if file_obj.node_type != File.NodeType.FOLDER:
            return Response(
                {'detail': 'Only folders can be synced.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        service = FileSyncService(log=logger)
        result = service.sync_folder_shallow(request.user, parent_db=file_obj)
        return Response({
            'files_created': result.files_created,
            'folders_created': result.folders_created,
            'files_soft_deleted': result.files_soft_deleted,
            'folders_soft_deleted': result.folders_soft_deleted,
            'errors': result.errors,
        })
