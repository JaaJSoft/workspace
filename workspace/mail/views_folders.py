import logging

from django.db import transaction
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.booleans import is_truthy
from workspace.common.logging import scrub
from workspace.common.uuids import parse_uuid_or_none

from .models import MailAccount, MailFolder, MailMessage
from .serializers import (
    MailFolderCreateSerializer,
    MailFolderSerializer,
    MailFolderUpdateSerializer,
)

logger = logging.getLogger(__name__)


@extend_schema(tags=["Mail"])
class MailFolderListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="List folders for a mail account",
        parameters=[OpenApiParameter("account", str, required=True)],
    )
    def get(self, request):
        account_id = request.query_params.get("account")
        if not account_id:
            return Response(
                {"detail": "account query parameter is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        account_uuid = parse_uuid_or_none(account_id)
        if account_uuid is None:
            # Malformed UUID on a collection filter -> 400 (per CLAUDE.md
            # "Query parameter parsing"). 404 is reserved for well-formed
            # UUIDs that don't resolve to an accessible account.
            return Response(
                {"detail": '"account" must be a valid UUID.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            account = MailAccount.objects.get(uuid=account_uuid, owner=request.user)
        except MailAccount.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        folders = MailFolder.objects.filter(account=account)
        if not is_truthy(request.query_params.get("show_hidden")):
            folders = folders.filter(is_hidden=False)
        return Response(MailFolderSerializer(folders, many=True).data)

    @extend_schema(summary="Create a folder", request=MailFolderCreateSerializer)
    def post(self, request):
        ser = MailFolderCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        try:
            account = MailAccount.objects.get(
                uuid=ser.validated_data["account_id"],
                owner=request.user,
            )
        except MailAccount.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        from .services.imap_folders import create_folder

        try:
            folder = create_folder(
                account,
                ser.validated_data["name"],
                parent_name=ser.validated_data.get("parent_name", ""),
            )
            return Response(
                MailFolderSerializer(folder).data,
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            logger.warning(
                "Failed to create folder for %s: %s", scrub(account.email), scrub(e)
            )
            return Response(
                {"detail": "Failed to create folder"},
                status=status.HTTP_502_BAD_GATEWAY,
            )


@extend_schema(tags=["Mail"])
class MailFolderUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_folder(self, request, uuid):
        try:
            folder = MailFolder.objects.select_related("account").get(uuid=uuid)
        except MailFolder.DoesNotExist:
            return None
        if folder.account.owner != request.user:
            return None
        return folder

    @extend_schema(
        summary="Update folder (icon, color, rename, move)",
        request=MailFolderUpdateSerializer,
    )
    def patch(self, request, uuid):
        folder = self._get_folder(request, uuid)
        if not folder:
            return Response(status=status.HTTP_404_NOT_FOUND)

        ser = MailFolderUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        # Reject hiding special folders BEFORE any IMAP operations: a 400
        # returned after a successful rename/move would leave IMAP and DB
        # out of sync, with no client-visible signal that the rename happened.
        if ser.validated_data.get("is_hidden") and folder.folder_type != "other":
            return Response(
                {"detail": "Cannot hide a special folder"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Move folder if parent_name is provided
        parent_name = ser.validated_data.pop("parent_name", None)
        if parent_name is not None:
            if folder.folder_type != "other":
                return Response(
                    {"detail": "Cannot move a special folder"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            from .services.imap_folders import move_folder

            try:
                move_folder(folder.account, folder, parent_name)
            except Exception as e:
                logger.warning(
                    "Failed to move folder for %s: %s",
                    scrub(folder.account.email),
                    scrub(e),
                )
                return Response(
                    {"detail": "Failed to move folder"},
                    status=status.HTTP_502_BAD_GATEWAY,
                )
        else:
            # Rename on IMAP if display_name changed (only when not moving)
            new_display_name = ser.validated_data.pop("display_name", None)
            if new_display_name and new_display_name != folder.display_name:
                from .services.imap_folders import rename_folder

                # rename_folder needs the FULL mailbox name. For nested folders
                # (Parent/Old) we must keep the parent path so we end up with
                # Parent/New, not New at the root.
                delimiter = folder.account.imap_delimiter or "/"
                parent_path, _, _ = folder.name.rpartition(delimiter)
                new_name = (
                    f"{parent_path}{delimiter}{new_display_name}"
                    if parent_path
                    else new_display_name
                )

                try:
                    rename_folder(folder.account, folder, new_name)
                except Exception as e:
                    logger.warning(
                        "Failed to rename folder for %s: %s",
                        scrub(folder.account.email),
                        scrub(e),
                    )
                    return Response(
                        {"detail": "Failed to rename folder"},
                        status=status.HTTP_502_BAD_GATEWAY,
                    )

        # Update icon/color/is_hidden locally
        update_fields = ["updated_at"]
        for field in ("icon", "color", "is_hidden"):
            if field in ser.validated_data:
                setattr(folder, field, ser.validated_data[field])
                update_fields.append(field)
        if len(update_fields) > 1:
            folder.save(update_fields=update_fields)

        return Response(MailFolderSerializer(folder).data)

    @extend_schema(summary="Delete a folder")
    def delete(self, request, uuid):
        folder = self._get_folder(request, uuid)
        if not folder:
            return Response(status=status.HTTP_404_NOT_FOUND)

        # Prevent deletion of special folders
        if folder.folder_type != "other":
            return Response(
                {"detail": "Cannot delete a special folder"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from .services.imap_folders import delete_folder

        try:
            delete_folder(folder.account, folder)
        except Exception as e:
            logger.warning(
                "Failed to delete folder for %s: %s",
                scrub(folder.account.email),
                scrub(e),
            )
            return Response(
                {"detail": "Failed to delete folder"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # Clear selection if this folder was selected
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Mail"])
class MailFolderMarkReadView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Mark all messages in a folder as read")
    @transaction.atomic
    def post(self, request, uuid):
        try:
            folder = MailFolder.objects.select_related("account").get(uuid=uuid)
        except MailFolder.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if folder.account.owner != request.user:
            return Response(status=status.HTTP_404_NOT_FOUND)

        qs = MailMessage.objects.filter(
            folder=folder,
            is_read=False,
            deleted_at__isnull=True,
        )
        # Capture pks BEFORE the update so we can refresh denormalized
        # MailLabel.unread_count for any label attached to these messages.
        # Without this, label badges in the sidebar stay stale until the next
        # user-side toggle.
        affected_ids = list(qs.values_list("pk", flat=True))
        updated = qs.update(is_read=True)

        folder.unread_count = 0
        folder.save(update_fields=["unread_count", "updated_at"])

        from .services.label_counts import refresh_labels_for_messages

        refresh_labels_for_messages(affected_ids)

        return Response({"updated": updated})
