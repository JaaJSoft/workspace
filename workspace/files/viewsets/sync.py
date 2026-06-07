"""Sync + lock actions for FileViewSet."""

import logging
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from workspace.files.models import File
from workspace.files.services import FileService

logger = logging.getLogger(__name__)


class SyncMixin:
    """Adds sync_root, sync_folder, lock actions."""

    LOCK_TTL = timedelta(minutes=5)

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
    @action(detail=False, methods=["post"], url_path="sync")
    def sync_root(self, request):
        """Sync root-level files for the current user."""
        from workspace.files.sync import FileSyncService

        service = FileSyncService(log=logger)
        result = service.sync_folder_shallow(request.user, parent_db=None)
        for err in result.errors:
            logger.warning("sync root: %s", err)
        return Response(
            {
                "files_created": result.files_created,
                "folders_created": result.folders_created,
                "files_soft_deleted": result.files_soft_deleted,
                "folders_soft_deleted": result.folders_soft_deleted,
                "error_count": len(result.errors),
            }
        )

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
    @action(detail=True, methods=["post"], url_path="sync")
    def sync_folder(self, request, uuid=None):
        """Sync a specific folder's children for the current user."""
        from workspace.files.sync import FileSyncService

        file_obj = self.get_object()
        if file_obj.node_type != File.NodeType.FOLDER:
            return Response(
                {"detail": "Only folders can be synced."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        service = FileSyncService(log=logger)
        result = service.sync_folder_shallow(request.user, parent_db=file_obj)
        for err in result.errors:
            logger.warning("sync folder %s: %s", uuid, err)
        return Response(
            {
                "files_created": result.files_created,
                "folders_created": result.folders_created,
                "files_soft_deleted": result.files_soft_deleted,
                "folders_soft_deleted": result.folders_soft_deleted,
                "error_count": len(result.errors),
            }
        )

    @action(detail=True, methods=["get", "post", "delete"], url_path="lock")
    def lock(self, request, uuid=None):
        # Gate on access (owned + group + shared-with). Without this filter
        # any authenticated user with a UUID could probe lock state and
        # acquire/release locks on files they have no rights to.
        file_obj = (
            File.objects.filter(
                FileService.accessible_files_q(request.user),
                uuid=uuid,
                deleted_at__isnull=True,
            )
            .select_related("locked_by")
            .first()
        )
        if not file_obj:
            return Response(status=status.HTTP_404_NOT_FOUND)

        now = timezone.now()

        def _lock_response(f):
            if f.locked_by_id is None:
                return Response(
                    {
                        "locked_by": None,
                        "locked_at": None,
                        "lock_expires_at": None,
                        "is_expired": True,
                    }
                )
            return Response(
                {
                    "locked_by": {
                        "id": f.locked_by.pk,
                        "username": f.locked_by.username,
                    },
                    "locked_at": f.locked_at,
                    "lock_expires_at": f.lock_expires_at,
                    # ``<= now`` matches the acquire predicate
                    # (``Q(lock_expires_at__lte=now)``) and ``is_locked()`` /
                    # the PATCH guard's ``> now`` complement, so a client
                    # never sees ``is_expired=False`` for a lock that an
                    # acquire would simultaneously treat as free.
                    "is_expired": f.lock_expires_at is not None
                    and f.lock_expires_at <= now,
                }
            )

        if request.method == "GET":
            return _lock_response(file_obj)

        if request.method == "DELETE":
            # Release is allowed only when the lock is the requester's, or
            # already cleared / expired (in which case it's a cleanup no-op
            # callers can rely on). Anything else - someone else's active
            # lock - returns 403, otherwise the 409 that POST returns
            # against an active lock would be trivially bypassed by issuing
            # DELETE then POST.
            cleared = (
                File.objects.filter(pk=file_obj.pk)
                .filter(
                    Q(locked_by=request.user)
                    | Q(locked_by__isnull=True)
                    | Q(lock_expires_at__lte=now),
                )
                .update(
                    locked_by=None,
                    locked_at=None,
                    lock_expires_at=None,
                )
            )
            if not cleared:
                return Response(status=status.HTTP_403_FORBIDDEN)
            from workspace.files.sse_provider import push_file_event

            push_file_event(
                file_obj,
                "lock_released",
                request.user.username,
                exclude_user_id=request.user.pk,
            )
            return Response(status=status.HTTP_204_NO_CONTENT)

        # POST - acquire or renew
        if file_obj.locked_by_id == request.user.pk:
            # Renew
            File.objects.filter(pk=file_obj.pk).update(
                lock_expires_at=now + self.LOCK_TTL,
            )
            file_obj.refresh_from_db()
            return _lock_response(file_obj)

        # Acquire (unlocked or expired). The "is free?" predicate lives in
        # the UPDATE's WHERE clause so two concurrent acquires that both
        # read the row as free can't both win - the loser sees count=0
        # and returns 409 instead of silently overwriting the winner.
        acquired = (
            File.objects.filter(pk=file_obj.pk)
            .filter(Q(locked_by__isnull=True) | Q(lock_expires_at__lte=now))
            .update(
                locked_by=request.user,
                locked_at=now,
                lock_expires_at=now + self.LOCK_TTL,
            )
        )
        if not acquired:
            file_obj.refresh_from_db()
            return Response(
                _lock_response(file_obj).data,
                status=status.HTTP_409_CONFLICT,
            )
        file_obj.refresh_from_db()
        return _lock_response(file_obj)
