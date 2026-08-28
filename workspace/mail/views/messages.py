import logging

from django.db import transaction
from django.db.models import Count, Prefetch
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
from workspace.notifications.services.notifications import mark_sources_read

from ..models import MailFolder, MailLabel, MailMessage, MailMessageLabel
from ..queries import folder_group_ids, user_account_ids
from ..serializers import (
    BatchActionSerializer,
    MailMessageDetailSerializer,
    MailMessageListSerializer,
    MailMessageUpdateSerializer,
)

logger = logging.getLogger(__name__)


@extend_schema(tags=["Mail - Messages"])
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
            qs = MailMessage.objects.filter(
                folder_id__in=folder_group_ids(folder), deleted_at__isnull=True
            )
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
            from workspace.mail.search import fts_messages

            qs = fts_messages(qs, search)
        if is_truthy(request.query_params.get("unread")):
            qs = qs.filter(is_read=False)
        if is_truthy(request.query_params.get("starred")):
            qs = qs.filter(is_starred=True)
        if is_truthy(request.query_params.get("attachments")):
            qs = qs.filter(has_attachments=True)

        total = qs.count()
        order_fields = ("-search_rank", "-date") if search else ("-date",)
        messages = list(
            qs.annotate(attachments_count=Count("attachments"))
            .prefetch_related(
                Prefetch(
                    "message_labels",
                    queryset=MailMessageLabel.objects.select_related("label").order_by(
                        "label__position", "label__name"
                    ),
                )
            )
            .order_by(*order_fields)[offset : offset + page_size]
        )

        # Seeing the row in the list is seeing the mail arrive. Covers the
        # folder, label and inbox=all modes at once, which a folder-scoped
        # hook would not: the default mail view is a unified inbox.
        mark_sources_read(request.user, messages)

        return Response(
            {
                "results": MailMessageListSerializer(messages, many=True).data,
                "count": total,
                "page": page,
                "page_size": page_size,
            }
        )


@extend_schema(tags=["Mail - Messages"])
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

        from ..services.triage import set_flag

        for field, flags in (
            ("is_read", ("unread", "read")),
            ("is_starred", ("unstarred", "starred")),
        ):
            if field not in ser.validated_data:
                continue
            if not set_flag(msg, flags[bool(ser.validated_data[field])]):
                logger.warning("Failed to sync %s to IMAP for %s", field, msg.uuid)

        if "ai_summary" in ser.validated_data:
            msg.ai_summary = ser.validated_data["ai_summary"]
            msg.save(update_fields=["ai_summary", "updated_at"])

        return Response(MailMessageDetailSerializer(msg).data)

    @extend_schema(summary="Soft-delete a message")
    def delete(self, request, uuid):
        msg = self._get_message(request, uuid)
        if not msg:
            return Response(status=status.HTTP_404_NOT_FOUND)

        from ..services.counts import (
            refresh_folder_counts,
            refresh_message_label_counts,
        )
        from ..services.imap_messages import delete_message
        from ..services.notifications import settle_message_notifications

        with transaction.atomic():
            msg.deleted_at = timezone.now()
            msg.save(update_fields=["deleted_at", "updated_at"])
            refresh_folder_counts(msg.folder)
            refresh_message_label_counts(msg)

        # Soft delete never CASCADEs the row's Notification, and a deleted
        # message can never again appear on a rendered page for
        # mark_sources_read to catch it - settle it here instead.
        settle_message_notifications(request.user, [msg.pk])

        try:
            delete_message(msg.account, msg)
        except Exception:
            logger.warning("Failed to delete message on IMAP for %s", msg.uuid)

        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Mail - Messages"])
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

        from ..services.imap_messages import delete_message
        from ..services.triage import flag_operations, move_to_folder

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

        # Same table the single-message path uses, keyed by this endpoint's
        # own action names.
        flags = flag_operations()
        action_map = {
            "mark_read": flags["read"],
            "mark_unread": flags["unread"],
            "star": flags["starred"],
            "unstar": flags["unstarred"],
        }

        processed = 0
        affected_folders = set()
        to_bulk_update = []
        bulk_update_fields = set()
        deleted_pks = []
        for msg in messages:
            affected_folders.add(msg.folder_id)
            try:
                if action == "delete":
                    msg.deleted_at = timezone.now()
                    msg.save(update_fields=["deleted_at", "updated_at"])
                    deleted_pks.append(msg.pk)
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
                        # refresh=False: the counters are recomputed once for
                        # the whole batch below.
                        move_to_folder(msg, target_folder, refresh=False)
                    except Exception as e:
                        logger.warning(
                            "IMAP move failed for message %s: %s", msg.uuid, scrub(e)
                        )
                        continue
                    # Use .pk to match msg.folder_id added above. refresh_folders_counts_bulk
                    # filters via folder_id__in, so a UUID would never match.
                    affected_folders.add(target_folder.pk)
                elif action in action_map:
                    imap_fn, field, value = action_map[action]
                    setattr(msg, field, value)
                    bulk_update_fields.add(field)
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

        from ..services.counts import refresh_folders_counts_bulk
        from ..services.label_counts import refresh_labels_for_messages
        from ..services.notifications import settle_message_notifications

        with transaction.atomic():
            if to_bulk_update:
                MailMessage.objects.bulk_update(
                    to_bulk_update, list(bulk_update_fields)
                )

            # Refresh counts for all affected folders in a single batch:
            # 1 aggregate + 1 bulk_update instead of 2N queries.
            refresh_folders_counts_bulk(affected_folders)

            # Refresh label counts for read/unread/delete actions
            if action in ("mark_read", "mark_unread", "delete"):
                refresh_labels_for_messages([m.pk for m in messages])

        if deleted_pks:
            # Soft delete never CASCADEs the row's Notification, and a
            # deleted message can never again appear on a rendered page for
            # mark_sources_read to catch it - settle it here instead.
            settle_message_notifications(request.user, deleted_pks)

        return Response({"processed": processed})
