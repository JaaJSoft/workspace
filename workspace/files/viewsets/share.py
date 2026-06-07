"""Share-related actions for FileViewSet."""

from django.contrib.auth import get_user_model
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from workspace.files.models import FileShare, FileShareLink
from workspace.files.services import FileService
from workspace.files.services.sharing import (
    create_share_link as service_create_share_link,
)
from workspace.files.services.sharing import (
    revoke_share_link as service_revoke_share_link,
)
from workspace.files.services.sharing import (
    share_file as service_share_file,
)
from workspace.files.services.sharing import (
    unshare_file as service_unshare_file,
)
from workspace.notifications.services.notifications import notify

User = get_user_model()


class ShareMixin:
    """Adds share, shares, share_links, delete_share_link actions."""

    @extend_schema(
        summary="Share or unshare a file",
        description="POST to share a file with a user, DELETE to remove the share. Only files can be shared (not folders).",
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "shared_with": {
                        "type": "integer",
                        "description": "User ID to share with / unshare from",
                    },
                },
                "required": ["shared_with"],
            },
        },
        responses={
            201: OpenApiResponse(description="Share created."),
            200: OpenApiResponse(description="Share removed or already exists."),
            400: OpenApiResponse(description="Bad request."),
            404: OpenApiResponse(description="File or user not found."),
        },
    )
    @action(detail=True, methods=["post", "delete"], url_path="share")
    def share(self, request, uuid=None):
        """Share or unshare a file with another user (files only)."""
        from workspace.files.actions import ActionRegistry

        file_obj = self.get_object()
        perm = FileService.get_permission(request.user, file_obj)

        if not ActionRegistry.is_action_available(
            "share",
            request.user,
            file_obj,
            permission=perm,
        ):
            return Response(
                {"detail": "Only files can be shared."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        shared_with_id = request.data.get("shared_with")
        if not shared_with_id:
            return Response(
                {"detail": "shared_with is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            shared_with_id = int(shared_with_id)
        except TypeError, ValueError:
            return Response(
                {"detail": "shared_with must be a valid user id."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if shared_with_id == request.user.pk:
            return Response(
                {"detail": "Cannot share with yourself."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            target_user = User.objects.get(pk=shared_with_id, is_active=True)
        except User.DoesNotExist:
            return Response(
                {"detail": "User not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if request.method == "POST":
            permission = request.data.get("permission", FileShare.Permission.READ_ONLY)
            if permission not in (
                FileShare.Permission.READ_ONLY,
                FileShare.Permission.READ_WRITE,
            ):
                return Response(
                    {"detail": 'Invalid permission. Must be "ro" or "rw".'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            share, created, permission_changed = service_share_file(
                file_obj,
                target_user=target_user,
                permission=permission,
                acting_user=request.user,
            )
            if created:
                notify(
                    recipient=target_user,
                    origin="files",
                    title=f'{request.user.username} shared "{file_obj.name}" with you',
                    url=f"/files/{file_obj.parent_id}"
                    if file_obj.parent_id
                    else "/files",
                    actor=request.user,
                )
            if permission_changed:
                perm_label = (
                    "read & write"
                    if permission == FileShare.Permission.READ_WRITE
                    else "read only"
                )
                notify(
                    recipient=target_user,
                    origin="files",
                    title=f'Permission updated to {perm_label} on "{file_obj.name}"',
                    url=f"/files/{file_obj.parent_id}"
                    if file_obj.parent_id
                    else "/files",
                    actor=request.user,
                )
            return Response(
                {"shared": True, "permission": share.permission},
                status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            )

        # DELETE
        deleted = service_unshare_file(
            file_obj,
            target_user=target_user,
            acting_user=request.user,
        )
        if deleted:
            notify(
                recipient=target_user,
                origin="files",
                title=f'{request.user.username} revoked your access to "{file_obj.name}"',
                actor=request.user,
            )
        if not deleted:
            return Response(
                {"detail": "Share not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response({"shared": False}, status=status.HTTP_200_OK)

    @extend_schema(
        summary="List shares for a file",
        description="Return users who have access to this file via sharing.",
        responses={
            200: OpenApiResponse(description="List of shares."),
        },
    )
    @action(detail=True, methods=["get"], url_path="shares")
    def shares(self, request, uuid=None):
        """List users a file is shared with."""
        file_obj = self.get_object()
        share_qs = FileShare.objects.filter(file=file_obj).select_related("shared_with")
        results = [
            {
                "id": s.shared_with.pk,
                "username": s.shared_with.username,
                "first_name": s.shared_with.first_name,
                "last_name": s.shared_with.last_name,
                "permission": s.permission,
                "shared_at": s.created_at,
            }
            for s in share_qs
        ]
        return Response(results)

    @action(detail=True, methods=["get", "post"], url_path="share-links")
    def share_links(self, request, uuid=None):
        """List or create share links for a file (owner only)."""
        from django.http import Http404

        from workspace.files.models import File

        file_obj = self.get_object()
        # get_queryset returns owned + group + shared files, so explicitly
        # gate this owner-only action.
        if file_obj.owner_id != request.user.id:
            raise Http404

        if request.method == "GET":
            links = FileShareLink.objects.filter(file=file_obj)
            data = [self._serialize_share_link(link, request) for link in links]
            return Response(data)

        # POST - create
        if file_obj.node_type != File.NodeType.FILE:
            return Response(
                {"detail": "Share links can only be created for files."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        link = service_create_share_link(
            file_obj,
            acting_user=request.user,
            password=request.data.get("password", ""),
            expires_at=request.data.get("expires_at"),
        )
        return Response(
            self._serialize_share_link(link, request),
            status=status.HTTP_201_CREATED,
        )

    @action(
        detail=True, methods=["delete"], url_path=r"share-links/(?P<link_uuid>[^/.]+)"
    )
    def delete_share_link(self, request, uuid=None, link_uuid=None):
        """Revoke (delete) a share link (owner only)."""
        from django.http import Http404

        file_obj = self.get_object()
        # get_queryset returns owned + group + shared files; only the owner
        # can revoke share links.
        if file_obj.owner_id != request.user.id:
            raise Http404

        deleted = service_revoke_share_link(
            file_obj,
            link_uuid=link_uuid,
            acting_user=request.user,
        )
        if not deleted:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @staticmethod
    def _format_dt(value):
        """Format a datetime value for API responses, handling both datetime objects and strings."""
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return value.isoformat()

    def _serialize_share_link(self, link, request):
        """Serialize a FileShareLink for API responses."""
        url = request.build_absolute_uri(f"/files/shared/{link.token}")
        return {
            "uuid": str(link.uuid),
            "token": link.token,
            "url": url,
            "has_password": link.has_password,
            "expires_at": self._format_dt(link.expires_at),
            "view_count": link.view_count,
            "last_accessed_at": self._format_dt(link.last_accessed_at),
            "created_at": self._format_dt(link.created_at),
        }
