import logging

from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.booleans import is_truthy
from workspace.common.logging import scrub
from workspace.common.mixins import CacheControlMixin
from workspace.common.uuids import parse_uuid_or_none

from .models import MailFolder, MailLabel, MailMessage, MailMessageLabel
from .queries import user_account_ids
from .serializers import (
    BatchActionSerializer,
    MailMessageDetailSerializer,
    MailMessageListSerializer,
    MailMessageUpdateSerializer,
)

logger = logging.getLogger(__name__)


@extend_schema(tags=["Mail"])
class MailMessageListView(CacheControlMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="List messages in a folder",
        parameters=[
            OpenApiParameter("folder", str, required=False),
            OpenApiParameter("label", str, required=False),
            OpenApiParameter(
                "inbox",
                str,
                required=False,
                description='Pass "all" to get messages from all inbox folders',
            ),
            OpenApiParameter("page", int, required=False),
            OpenApiParameter("search", str, required=False),
            OpenApiParameter("unread", bool, required=False),
            OpenApiParameter("starred", bool, required=False),
            OpenApiParameter("attachments", bool, required=False),
        ],
    )
    def get(self, request):
        folder_id = request.query_params.get("folder")
        label_id = request.query_params.get("label")
        inbox_mode = request.query_params.get("inbox")

        if not folder_id and not label_id and inbox_mode != "all":
            return Response(
                {"detail": "folder, label, or inbox=all query parameter is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        folder = None
        label = None

        if label_id:
            label_uuid = parse_uuid_or_none(label_id)
            if label_uuid is None:
                # Malformed UUID on a collection filter -> 400 (per CLAUDE.md
                # "Query parameter parsing"). A well-formed UUID that doesn't
                # resolve still returns 404 below.
                return Response(
                    {"detail": '"label" must be a valid UUID.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                label = MailLabel.objects.select_related("account").get(uuid=label_uuid)
            except MailLabel.DoesNotExist:
                return Response(status=status.HTTP_404_NOT_FOUND)
            if label.account.owner != request.user:
                return Response(status=status.HTTP_404_NOT_FOUND)

        if folder_id:
            folder_uuid = parse_uuid_or_none(folder_id)
            if folder_uuid is None:
                return Response(
                    {"detail": '"folder" must be a valid UUID.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                folder = MailFolder.objects.select_related("account").get(
                    uuid=folder_uuid
                )
            except MailFolder.DoesNotExist:
                return Response(status=status.HTTP_404_NOT_FOUND)
            if folder.account.owner != request.user:
                return Response(status=status.HTTP_404_NOT_FOUND)

        # Build base queryset
        if inbox_mode == "all" and not folder and not label:
            qs = MailMessage.objects.filter(
                account_id__in=user_account_ids(request.user),
                folder__folder_type=MailFolder.FolderType.INBOX,
                deleted_at__isnull=True,
            )
        elif folder:
            qs = MailMessage.objects.filter(folder=folder, deleted_at__isnull=True)
        else:
            # label-only: cross-folder for the label's account
            qs = MailMessage.objects.filter(
                account=label.account, deleted_at__isnull=True
            )

        if label:
            qs = qs.filter(message_labels__label=label)

        # Accept any input but fall back to page 1 for non-numeric, zero, or
        # negative values. A negative offset would otherwise hit Django's
        # "Negative indexing is not supported" and surface as a 500.
        try:
            page = int(request.query_params.get("page", 1))
            if page < 1:
                page = 1
        except TypeError, ValueError:
            page = 1
        page_size = 50
        offset = (page - 1) * page_size

        # Apply optional filters
        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(subject__icontains=search)
                | Q(snippet__icontains=search)
                | Q(from_email__icontains=search)
                | Q(from_name__icontains=search)
            )
        if is_truthy(request.query_params.get("unread")):
            qs = qs.filter(is_read=False)
        if is_truthy(request.query_params.get("starred")):
            qs = qs.filter(is_starred=True)
        if is_truthy(request.query_params.get("attachments")):
            qs = qs.filter(has_attachments=True)

        total = qs.count()
        messages = (
            qs.annotate(attachments_count=Count("attachments"))
            .prefetch_related(
                Prefetch(
                    "message_labels",
                    queryset=MailMessageLabel.objects.select_related("label").order_by(
                        "label__position", "label__name"
                    ),
                )
            )
            .order_by("-date")[offset : offset + page_size]
        )

        return Response(
            {
                "results": MailMessageListSerializer(messages, many=True).data,
                "count": total,
                "page": page,
                "page_size": page_size,
            }
        )


@extend_schema(tags=["Mail"])
class MailMessageDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_message(self, request, uuid):
        try:
            msg = (
                MailMessage.objects.select_related("account", "folder")
                .prefetch_related(
                    "attachments",
                    Prefetch(
                        "message_labels",
                        queryset=MailMessageLabel.objects.select_related(
                            "label"
                        ).order_by("label__position", "label__name"),
                    ),
                )
                .get(uuid=uuid, deleted_at__isnull=True)
            )
        except MailMessage.DoesNotExist:
            return None
        if msg.account.owner != request.user:
            return None
        return msg

    @extend_schema(summary="Get full message details")
    def get(self, request, uuid):
        msg = self._get_message(request, uuid)
        if not msg:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(MailMessageDetailSerializer(msg).data)

    @extend_schema(summary="Update message flags", request=MailMessageUpdateSerializer)
    def patch(self, request, uuid):
        msg = self._get_message(request, uuid)
        if not msg:
            return Response(status=status.HTTP_404_NOT_FOUND)

        ser = MailMessageUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        from .services.imap_messages import (
            mark_read,
            mark_unread,
            star_message,
            unstar_message,
        )

        if "is_read" in ser.validated_data:
            val = ser.validated_data["is_read"]
            msg.is_read = val
            try:
                if val:
                    mark_read(msg.account, msg)
                else:
                    mark_unread(msg.account, msg)
            except Exception:
                logger.warning("Failed to sync read flag to IMAP for %s", msg.uuid)

        if "is_starred" in ser.validated_data:
            val = ser.validated_data["is_starred"]
            msg.is_starred = val
            try:
                if val:
                    star_message(msg.account, msg)
                else:
                    unstar_message(msg.account, msg)
            except Exception:
                logger.warning("Failed to sync star flag to IMAP for %s", msg.uuid)

        if "ai_summary" in ser.validated_data:
            msg.ai_summary = ser.validated_data["ai_summary"]

        from .views import _refresh_folder_counts, _refresh_message_label_counts

        with transaction.atomic():
            msg.save()
            _refresh_folder_counts(msg.folder)
            if "is_read" in ser.validated_data:
                _refresh_message_label_counts(msg)
        return Response(MailMessageDetailSerializer(msg).data)

    @extend_schema(summary="Soft-delete a message")
    def delete(self, request, uuid):
        msg = self._get_message(request, uuid)
        if not msg:
            return Response(status=status.HTTP_404_NOT_FOUND)

        from .services.imap_messages import delete_message
        from .views import _refresh_folder_counts, _refresh_message_label_counts

        with transaction.atomic():
            msg.deleted_at = timezone.now()
            msg.save(update_fields=["deleted_at", "updated_at"])
            _refresh_folder_counts(msg.folder)
            _refresh_message_label_counts(msg)

        try:
            delete_message(msg.account, msg)
        except Exception:
            logger.warning("Failed to delete message on IMAP for %s", msg.uuid)

        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Mail"])
class MailBatchActionView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Batch action on messages", request=BatchActionSerializer)
    def post(self, request):
        ser = BatchActionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        message_ids = ser.validated_data["message_ids"]
        action = ser.validated_data["action"]

        messages = MailMessage.objects.filter(
            uuid__in=message_ids,
            account_id__in=user_account_ids(request.user),
            deleted_at__isnull=True,
        ).select_related("account", "folder")

        from .services.imap_messages import (
            delete_message,
            mark_read,
            mark_unread,
            move_message,
            star_message,
            unstar_message,
        )

        # Resolve target folder for move action
        target_folder = None
        if action == "move":
            target_folder_id = ser.validated_data.get("target_folder_id")
            try:
                target_folder = MailFolder.objects.select_related("account").get(
                    uuid=target_folder_id
                )
            except MailFolder.DoesNotExist:
                return Response(
                    {"detail": "Target folder not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if target_folder.account.owner != request.user:
                return Response(status=status.HTTP_404_NOT_FOUND)

        action_map = {
            "mark_read": (mark_read, {"is_read": True}),
            "mark_unread": (mark_unread, {"is_read": False}),
            "star": (star_message, {"is_starred": True}),
            "unstar": (unstar_message, {"is_starred": False}),
        }

        processed = 0
        affected_folders = set()
        to_bulk_update = []
        bulk_update_fields = set()
        for msg in messages:
            affected_folders.add(msg.folder_id)
            try:
                if action == "delete":
                    msg.deleted_at = timezone.now()
                    msg.save(update_fields=["deleted_at", "updated_at"])
                    try:
                        delete_message(msg.account, msg)
                    except Exception as e:
                        logger.warning(
                            "IMAP delete failed for message %s: %s", msg.uuid, scrub(e)
                        )
                elif action == "move" and target_folder:
                    if target_folder.account_id != msg.account_id:
                        continue
                    try:
                        move_message(msg.account, msg, target_folder)
                    except Exception as e:
                        # Skip the local DB update so this row stays consistent
                        # with what IMAP actually has. Updating msg.folder here
                        # would create a split-brain state: the next sync would
                        # find the message still in the source folder server
                        # side and soft-delete the (now mis-located) row.
                        logger.warning(
                            "IMAP move failed for message %s: %s", msg.uuid, scrub(e)
                        )
                        continue
                    msg.folder = target_folder
                    msg.save(update_fields=["folder", "updated_at"])
                    # Use .pk to match msg.folder_id added above. _refresh_folders_counts_bulk
                    # filters via folder_id__in, so a UUID would never match.
                    affected_folders.add(target_folder.pk)
                elif action in action_map:
                    imap_fn, db_update = action_map[action]
                    for key, value in db_update.items():
                        setattr(msg, key, value)
                    bulk_update_fields.update(db_update.keys())
                    to_bulk_update.append(msg)
                    try:
                        imap_fn(msg.account, msg)
                    except Exception as e:
                        logger.warning(
                            "IMAP %s failed for message %s: %s",
                            scrub(action),
                            msg.uuid,
                            scrub(e),
                        )
                processed += 1
            except Exception:
                logger.warning(
                    "Batch action '%s' failed for message %s", scrub(action), msg.uuid
                )

        from .services.label_counts import refresh_labels_for_messages
        from .views import _refresh_folders_counts_bulk

        with transaction.atomic():
            if to_bulk_update:
                MailMessage.objects.bulk_update(
                    to_bulk_update, list(bulk_update_fields)
                )

            # Refresh counts for all affected folders in a single batch:
            # 1 aggregate + 1 bulk_update instead of 2N queries.
            _refresh_folders_counts_bulk(affected_folders)

            # Refresh label counts for read/unread/delete actions
            if action in ("mark_read", "mark_unread", "delete"):
                refresh_labels_for_messages([m.pk for m in messages])

        return Response({"processed": processed})
