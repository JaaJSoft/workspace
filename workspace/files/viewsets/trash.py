"""Trash actions for FileViewSet: restore, purge, trash list, clean."""

from datetime import timedelta

from django.conf import settings
from django.db.models import OuterRef, Subquery
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from workspace.files.models import File, FileShare
from workspace.files.serializers import FileSerializer
from workspace.files.services import FileService

TRASH_RETENTION_DAYS = getattr(settings, "TRASH_RETENTION_DAYS", 30)


class TrashMixin:
    """Adds restore, purge, trash, clean_trash actions."""

    @extend_schema(
        summary="Restore from trash",
        description="Restore a previously trashed file or folder.",
        responses={
            200: OpenApiResponse(description="Item restored."),
            400: OpenApiResponse(description="Item is not in trash."),
        },
    )
    @action(detail=True, methods=["post"], url_path="restore")
    def restore(self, request, uuid=None):
        """Restore a file or folder from trash."""
        file_obj = self.get_object()
        if file_obj.deleted_at is None:
            return Response(
                {"detail": "Item is not in trash."}, status=status.HTTP_400_BAD_REQUEST
            )
        restored = FileService.restore(file_obj, acting_user=request.user)
        return Response({"restored": restored}, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Permanently delete",
        description="Permanently delete a trashed file or folder. This action is irreversible.",
        responses={
            204: OpenApiResponse(description="Item permanently deleted."),
            400: OpenApiResponse(description="Item is not in trash."),
        },
    )
    @action(detail=True, methods=["delete"], url_path="purge")
    def purge(self, request, uuid=None):
        """Permanently delete a file or folder."""
        file_obj = self.get_object()
        if file_obj.deleted_at is None:
            return Response(
                {"detail": "Item is not in trash."}, status=status.HTTP_400_BAD_REQUEST
            )
        FileService.hard_delete(file_obj, acting_user=request.user)
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
    @action(detail=False, methods=["get"], url_path="trash")
    def trash(self, request):
        """List trashed files and folders."""
        queryset = File.objects.filter(owner=request.user, deleted_at__isnull=False)
        queryset = FileService.annotate_for_serializer(queryset, request.user).annotate(
            user_share_permission=Subquery(
                FileShare.objects.filter(
                    file_id=OuterRef("pk"),
                    shared_with=request.user,
                ).values("permission")[:1]
            ),
        )
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
    @action(detail=False, methods=["delete"], url_path="trash/clean")
    def clean_trash(self, request):
        """Permanently delete trashed items past retention (or force all)."""
        force_value = self.request.query_params.get("force")
        force = (
            str(force_value).lower() in {"1", "true", "yes"}
            if force_value is not None
            else False
        )
        retention_days = TRASH_RETENTION_DAYS
        cutoff = timezone.now() - timedelta(days=retention_days)
        queryset = File.objects.filter(owner=request.user, deleted_at__isnull=False)
        if not force:
            queryset = queryset.filter(deleted_at__lt=cutoff)
        # Count Files explicitly so the response reflects deleted *files*,
        # not the cascaded total (which inflates the number with related
        # rows like FileShare, comments, etc).
        file_count = queryset.count()
        # select_related('owner') avoids N+1 in the pre_delete signal,
        # which reads instance.owner.username for each File.
        queryset.select_related("owner").delete()
        return Response(
            {
                "deleted": file_count,
                "retention_days": retention_days,
                "force": force,
            },
            status=status.HTTP_200_OK,
        )
