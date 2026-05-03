"""Favorite + pin actions for FileViewSet."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from workspace.files.models import File, FileFavorite, PinnedFolder
from workspace.files.services import FileService


class FavoritesMixin:
    """Adds favorite, pin, pinned, pinned_reorder actions."""

    @extend_schema(
        summary="Favorite or unfavorite a file/folder",
        description="POST to add to favorites, DELETE to remove from favorites.",
        responses={
            200: OpenApiResponse(description="Favorite status updated."),
        },
    )
    @action(detail=True, methods=['post', 'delete'], url_path='favorite')
    def favorite(self, request, uuid=None):
        """Add or remove a file/folder from favorites."""
        from workspace.files.actions import ActionRegistry
        file_obj, perm = self._resolve_file_with_access(uuid)
        if not ActionRegistry.is_action_available(
            'toggle_favorite', request.user, file_obj,
            permission=perm,
        ):
            return Response(status=status.HTTP_403_FORBIDDEN)
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
        from workspace.files.actions import ActionRegistry
        file_obj = self.get_object()
        # Distinguish "wrong shape" (400) from "not allowed for this user/state"
        # (403). The action also blocks group-root folders, deleted files and
        # users without EDIT permission, so the 400 message would have lied.
        if file_obj.node_type != File.NodeType.FOLDER:
            return Response({'detail': 'Only folders can be pinned.'}, status=status.HTTP_400_BAD_REQUEST)
        perm = FileService.get_permission(request.user, file_obj)
        if not ActionRegistry.is_action_available(
            'toggle_pin', request.user, file_obj,
            permission=perm,
        ):
            return Response(status=status.HTTP_403_FORBIDDEN)
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
            200: OpenApiResponse(description="List of pinned folders."),
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
        # Use the access-aware filter so pinned group subfolders are included.
        # ``self.get_queryset()`` is owner-scoped (group__isnull=True) and would
        # silently drop any pinned group folder.
        queryset = FileService.annotate_for_serializer(
            File.objects.filter(
                FileService.accessible_files_q(request.user),
                pk__in=folder_ids,
                deleted_at__isnull=True,
            ),
            request.user,
        )
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
        to_update = []
        for position, uuid in enumerate(order):
            if uuid in pinned_map:
                pin = pinned_map[uuid]
                if pin.position != position:
                    pin.position = position
                    to_update.append(pin)

        if to_update:
            PinnedFolder.objects.bulk_update(to_update, ['position'])

        return Response({'success': True}, status=status.HTTP_200_OK)
