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

from workspace.common.logging import scrub
from workspace.files.models import File
from workspace.files.queries import FilePermission, accessible_files_q, get_permission
from workspace.files.services import disk_sync

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
        result = disk_sync.sync_folder_level(request.user, log=logger)
        for err in result.errors:
            logger.warning("sync root: %s", scrub(err))
        return Response(
            {
                **result.counts(),
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
            400: OpenApiResponse(
                description="Not a folder, or not one of the caller's personal tree."
            ),
        },
    )
    @action(detail=True, methods=["post"], url_path="sync")
    def sync_folder(self, request, uuid=None):
        """Sync a specific folder's children for the current user."""
        file_obj = self.get_object()
        if file_obj.node_type != File.NodeType.FOLDER:
            return Response(
                {"detail": "Only folders can be synced."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = disk_sync.sync_folder_level(request.user, file_obj, log=logger)
        except disk_sync.NotPersonalFolder as refusal:
            return Response(
                {"detail": str(refusal)}, status=status.HTTP_400_BAD_REQUEST
            )
        for err in result.errors:
            logger.warning("sync folder %s: %s", scrub(uuid), scrub(err))
        return Response(
            {
                **result.counts(),
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
                accessible_files_q(request.user),
                uuid=uuid,
                deleted_at__isnull=True,
            )
            .select_related("locked_by")
            .first()
        )
        if not file_obj:
            return Response(status=status.HTTP_404_NOT_FOUND)
        # Taking or clearing the lock fences writers out, so it takes a right
        # to write - as for a WebDAV LOCK or a WOPI one. Reading it does not.
        if (
            request.method != "GET"
            and (get_permission(request.user, file_obj) or 0) < FilePermission.WRITE
        ):
            return Response(status=status.HTTP_403_FORBIDDEN)

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
                    lock_token="",
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

        # POST - renew or acquire. Each predicate lives in its UPDATE's WHERE
        # clause, not in the row read above: the lock can change hands in
        # between, and two requests that both read it as theirs or as free
        # can't both win - the loser sees count=0 and returns 409.
        # Renew only the caller's app lock (no token). A WOPI or DAV lock of
        # theirs keeps the deadline its protocol set.
        renewed = File.objects.filter(
            pk=file_obj.pk, locked_by=request.user, lock_token=""
        ).update(lock_expires_at=now + self.LOCK_TTL)
        # An app lock carries no token. Clearing the previous holder's is what
        # stops their WOPI or DAV client from refreshing or releasing this one.
        acquired = renewed or (
            File.objects.filter(pk=file_obj.pk)
            .filter(Q(locked_by__isnull=True) | Q(lock_expires_at__lte=now))
            .update(
                locked_by=request.user,
                locked_at=now,
                lock_expires_at=now + self.LOCK_TTL,
                lock_token="",
            )
        )
        file_obj.refresh_from_db()
        if not acquired and not (
            file_obj.locked_by_id == request.user.pk and file_obj.is_locked()
        ):
            return Response(
                _lock_response(file_obj).data,
                status=status.HTTP_409_CONFLICT,
            )
        return _lock_response(file_obj)
