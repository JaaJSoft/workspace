import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import OuterRef, Subquery
from django.http import Http404
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status, viewsets
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response

from workspace.common.mixins import CacheControlMixin
from workspace.files.services import FilePermission, FileService
from workspace.notifications.services.notifications import notify, notify_many

from .models import File, FileShare
from .serializers import FileSerializer
from .viewsets.actions import ActionsMixin
from .viewsets.comments import CommentsMixin
from .viewsets.content import ContentMixin
from .viewsets.copy import CopyMixin
from .viewsets.events import EventsMixin
from .viewsets.extract import ExtractMixin
from .viewsets.favorites import FavoritesMixin
from .viewsets.share import ShareMixin
from .viewsets.sync import SyncMixin
from .viewsets.trash import TrashMixin

logger = logging.getLogger(__name__)
User = get_user_model()

RECENT_FILES_LIMIT = getattr(settings, 'RECENT_FILES_LIMIT', 25)
RECENT_FILES_MAX_LIMIT = getattr(settings, 'RECENT_FILES_MAX_LIMIT', 200)


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
class FileViewSet(
    CacheControlMixin,
    CopyMixin,
    ExtractMixin,
    ContentMixin,
    TrashMixin,
    FavoritesMixin,
    SyncMixin,
    ShareMixin,
    CommentsMixin,
    ActionsMixin,
    EventsMixin,
    viewsets.ModelViewSet,
):
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
        'mime_type': ['exact'],
    }
    search_fields = ['name', 'mime_type']
    ordering_fields = ['name', 'created_at', 'updated_at', 'size']
    ordering = ['node_type', 'name']

    LOCK_TTL = timedelta(minutes=5)

    # ── Query helpers ─────────────────────────────────────────

    def filter_queryset(self, queryset):
        """Override to skip the parent filter when descendants mode is active."""
        if self._is_descendants_query() and 'parent' in self.request.query_params:
            # Temporarily hide 'parent' from query_params so DjangoFilterBackend
            # doesn't apply parent=exact (we handle it via path prefix in get_queryset)
            original = self.request.query_params
            mutable = original.copy()
            mutable.pop('parent')
            self.request._request.GET = mutable
            try:
                return super().filter_queryset(queryset)
            finally:
                self.request._request.GET = original
        return super().filter_queryset(queryset)

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

    def _is_descendants_query(self):
        return self.request.query_params.get('descendants', '').lower() in {'1', 'true'}

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

    def _resolve_file_with_access(self, uuid):
        """Resolve a file by UUID and check access.

        Returns (file_obj, permission).
        Raises Http404 if no access.
        """
        file_obj = File.objects.filter(uuid=uuid, deleted_at__isnull=True).first()
        if not file_obj:
            raise Http404
        perm = FileService.get_permission(self.request.user, file_obj)
        if perm is None:
            raise Http404
        return file_obj, perm

    @staticmethod
    def _file_etag(file_obj):
        """Deterministic ETag based on UUID and last modification time."""
        return f'"{file_obj.uuid}-{file_obj.updated_at.timestamp()}"'

    def _check_etag_304(self, request, file_obj):
        """Return a 304 response if the client's ETag matches, else None."""
        etag = self._file_etag(file_obj)
        if_none_match = request.META.get('HTTP_IF_NONE_MATCH')
        if if_none_match and if_none_match.strip('"') == etag.strip('"'):
            from django.http import HttpResponse as DjHttpResponse
            response = DjHttpResponse(status=304)
            response['ETag'] = etag
            response['Cache-Control'] = 'private, no-cache'
            return response
        return None

    @staticmethod
    def _set_file_cache_headers(response, file_obj):
        """Set ETag + Cache-Control; ConditionalGetMiddleware handles 304."""
        response['ETag'] = f'"{file_obj.uuid}-{file_obj.updated_at.timestamp()}"'
        response['Cache-Control'] = 'private, no-cache'

    def get_queryset(self):
        """Filter by current user's files."""
        user_share_subquery = FileShare.objects.filter(
            file_id=OuterRef('pk'),
            shared_with=self.request.user,
        ).values('permission')[:1]

        # Favorites: include owned, shared-with-me, and group files
        if self.action == 'list' and self._is_favorites_query():
            return FileService.annotate_for_serializer(
                File.objects.filter(
                    FileService.accessible_files_q(self.request.user),
                    deleted_at__isnull=True,
                    favorites__owner=self.request.user,
                ),
                self.request.user,
            ).annotate(
                user_share_permission=Subquery(user_share_subquery),
            ).distinct()

        # Resolve parent context: detect group from parent, resolve descendants
        parent_uuid = self.request.query_params.get('parent')
        group_id = self.request.query_params.get('group')
        ancestor_path = None

        if self.action == 'list' and parent_uuid:
            parent_obj = File.objects.filter(
                uuid=parent_uuid, deleted_at__isnull=True,
            ).values_list('group_id', 'path').first()
            if parent_obj:
                # Auto-detect group from parent
                if not group_id and parent_obj[0]:
                    group_id = parent_obj[0]
                # Descendants mode: use path prefix instead of parent=exact
                if self._is_descendants_query() and parent_obj[1]:
                    ancestor_path = parent_obj[1]
        if self.action == 'list' and group_id:
            if not self.request.user.groups.filter(id=group_id).exists():
                return File.objects.none()
            qs = File.objects.filter(
                group_id=group_id,
                deleted_at__isnull=True,
            )
            if ancestor_path:
                qs = qs.filter(path__startswith=ancestor_path + '/')
            qs = FileService.annotate_for_serializer(qs, self.request.user).annotate(
                user_share_permission=Subquery(user_share_subquery),
            )
            return qs

        queryset = File.objects.filter(owner=self.request.user, group__isnull=True)
        queryset = FileService.annotate_for_serializer(queryset, self.request.user).annotate(
            user_share_permission=Subquery(user_share_subquery),
        )
        if self.action in {'trash'} or self._is_trash_query():
            return queryset.filter(deleted_at__isnull=False)
        if self.action in {'restore', 'purge'}:
            return queryset
        queryset = queryset.filter(deleted_at__isnull=True)
        if self.action == 'list':
            if self._is_trash_query():
                return queryset
            if ancestor_path:
                return queryset.filter(path__startswith=ancestor_path + '/')
            if not self._is_recent_query() and 'parent' not in self.request.query_params:
                return queryset.filter(parent__isnull=True)
        return queryset

    def get_object(self):
        uuid = self.kwargs.get('uuid')
        try:
            return super().get_object()
        except Http404:
            # Only promote owner/group to full queryset object - shared
            # users are deliberately excluded so action-level fallbacks
            # can enforce restricted permissions (e.g. content-only writes).
            # The fallback queryset is annotated the same way as get_queryset()
            # so FileSerializer can render it without hitting missing-annotation
            # errors.
            file_obj = FileService.annotate_for_serializer(
                File.objects.filter(uuid=uuid, deleted_at__isnull=True),
                self.request.user,
            ).first()
            if file_obj and (FileService.get_permission(self.request.user, file_obj) or 0) >= FilePermission.EDIT:
                return file_obj
            raise

    # ── CRUD ──────────────────────────────────────────────────

    def create(self, request, *args, **kwargs):
        group_id = request.data.get('group')
        if group_id:
            if not request.user.groups.filter(id=group_id).exists():
                return Response(
                    {'group': 'You are not a member of this group.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
        return super().create(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())

        # Filter by tags (comma-separated UUIDs)
        tags_param = request.query_params.get('tags')
        if tags_param:
            tag_uuids = [u.strip() for u in tags_param.split(',') if u.strip()]
            if tag_uuids:
                queryset = queryset.filter(file_tags__tag__uuid__in=tag_uuids).distinct()

        # Filter by tag name (case-insensitive contains)
        tag_name_param = request.query_params.get('tag_name')
        if tag_name_param:
            queryset = queryset.filter(file_tags__tag__name__icontains=tag_name_param.strip()).distinct()

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

    def partial_update(self, request, *args, **kwargs):
        # Lock protection
        uuid = kwargs.get('uuid')
        locked_file = File.objects.filter(
            uuid=uuid, deleted_at__isnull=True,
        ).select_related('locked_by').only(
            'locked_by', 'lock_expires_at',
        ).first()
        if (
            locked_file
            and locked_file.locked_by_id is not None
            and locked_file.locked_by_id != request.user.pk
            and locked_file.lock_expires_at
            and locked_file.lock_expires_at > timezone.now()
        ):
            return Response(
                {
                    'detail': 'File is locked by another user.',
                    'locked_by': {
                        'id': locked_file.locked_by.pk,
                        'username': locked_file.locked_by.username,
                    },
                },
                status=423,
            )

        # Rename gate - consult the action registry so any future rule on
        # RenameAction.is_available (e.g., journal notes, shared files)
        # applies uniformly to direct PATCH calls, not just to UI menus.
        # Only fires when the user already has EDIT+ permission - below that,
        # the standard flow returns 404/403 and we must not preempt it
        # (e.g. a VIEW-only shared user must still see 404, not 403).
        if 'name' in request.data:
            from workspace.files.actions import ActionRegistry
            target = File.objects.filter(
                uuid=uuid, deleted_at__isnull=True,
            ).first()
            if target and request.data['name'] != target.name:
                perm = FileService.get_permission(request.user, target)
                if perm is not None and perm >= FilePermission.EDIT:
                    if not ActionRegistry.is_action_available(
                        'rename', request.user, target, permission=perm,
                    ):
                        return Response(
                            {'detail': 'Renaming this file is not allowed.'},
                            status=status.HTTP_403_FORBIDDEN,
                        )

        try:
            response = super().partial_update(request, *args, **kwargs)
            if response.status_code == 200 and ('content' in request.data or 'content' in request.FILES):
                from workspace.files.sse_provider import push_file_event
                updated_file = File.objects.filter(uuid=uuid).first()
                if updated_file:
                    push_file_event(updated_file, 'file_updated', request.user.username, exclude_user_id=request.user.pk)
            return response
        except Http404:
            # Check rw shared access
            uuid = kwargs.get('uuid')
            file_obj = FileService.annotate_for_serializer(
                File.objects.filter(uuid=uuid, deleted_at__isnull=True),
                request.user,
            ).first()
            if not file_obj or FileService.get_permission(request.user, file_obj) != FilePermission.WRITE:
                raise
            # Only allow content update - reject any other fields
            allowed_fields = {'content'}
            extra_fields = set(request.data.keys()) - allowed_fields
            if extra_fields:
                return Response(
                    {'detail': 'Shared write access only allows updating file content.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            # Perform the content update. The serializer's update() routes
            # the write through FileService.update_content, which records
            # the CONTENT_REPLACED event itself.
            serializer = self.get_serializer(file_obj, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            from workspace.files.sse_provider import push_file_event
            push_file_event(file_obj, 'file_updated', request.user.username, exclude_user_id=request.user.pk)
            notify(
                recipient=file_obj.owner,
                origin='files',
                title=f'{request.user.username} edited "{file_obj.name}"',
                url=f'/files/{file_obj.parent_id}' if file_obj.parent_id else '/files',
                actor=request.user,
            )
            return Response(serializer.data)

    def perform_destroy(self, instance):
        shared_users = User.objects.filter(
            received_shares__file=instance,
        )
        if shared_users.exists():
            recipients = list(shared_users)
            notify_many(
                recipients=recipients,
                origin='files',
                title=f'{self.request.user.username} deleted "{instance.name}"',
                actor=self.request.user,
            )
        FileService.soft_delete(instance, acting_user=self.request.user)
