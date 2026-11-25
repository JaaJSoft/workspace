import logging
import re

from django.db import transaction
from django.db.models import Prefetch
from django.http import HttpResponse
from django.utils.http import content_disposition_header
from django.utils.translation import gettext as _
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.booleans import is_truthy
from workspace.common.logging import scrub
from workspace.common.mixins import CacheControlMixin
from workspace.common.stored_content import protect_stored_content
from workspace.common.uuids import parse_uuid_or_none
from workspace.notifications.services.notifications import mark_sources_read

from ..models import MailFolder, MailMessage, MailMessageLabel
from ..queries import (
    canonical_folder,
    canonical_folder_id,
    folder_group_ids,
    reachable_message,
    user_account_ids,
    user_folders,
    user_labels,
    user_messages,
)
from ..serializers import (
    BatchActionSerializer,
    MailMessageDetailSerializer,
    MailMessageListSerializer,
    MailMessageUpdateSerializer,
)

logger = logging.getLogger(__name__)

EML_CONTENT_TYPE = "message/rfc822"


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
                {
                    "detail": _(
                        "A folder, label or inbox=all query parameter is required."
                    )
                },
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
                    {
                        "detail": _('"%(parameter)s" must be a valid UUID.')
                        % {"parameter": "label"}
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            label = (
                user_labels(request.user)
                .select_related("account")
                .filter(uuid=label_uuid)
                .first()
            )
            if label is None:
                return Response(status=status.HTTP_404_NOT_FOUND)

        if folder_id:
            folder_uuid = parse_uuid_or_none(folder_id)
            if folder_uuid is None:
                return Response(
                    {
                        "detail": _('"%(parameter)s" must be a valid UUID.')
                        % {"parameter": "folder"}
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            folder = (
                user_folders(request.user)
                .select_related("account")
                .filter(uuid=folder_uuid)
                .first()
            )
            if folder is None:
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
            qs.defer("body_text", "body_html")
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
        return (
            user_messages(request.user)
            .select_related("account", "folder")
            .prefetch_related(
                "attachments",
                Prefetch(
                    "message_labels",
                    queryset=MailMessageLabel.objects.select_related("label").order_by(
                        "label__position", "label__name"
                    ),
                ),
            )
            .filter(uuid=uuid, deleted_at__isnull=True)
            .first()
        )

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

        synced = True
        for field, flags in (
            ("is_read", ("unread", "read")),
            ("is_starred", ("unstarred", "starred")),
        ):
            if field not in ser.validated_data:
                continue
            if not set_flag(msg, flags[bool(ser.validated_data[field])]):
                synced = False
                logger.warning("Failed to sync %s to IMAP for %s", field, msg.uuid)

        if "ai_summary" in ser.validated_data:
            msg.ai_summary = ser.validated_data["ai_summary"]
            msg.save(update_fields=["ai_summary", "updated_at"])

        # synced: False when the server refused a flag the row now carries;
        # the next sync puts the server's value back.
        return Response({**MailMessageDetailSerializer(msg).data, "synced": synced})

    @extend_schema(summary="Move a message to Trash, or delete it for good from Trash")
    def delete(self, request, uuid):
        msg = self._get_message(request, uuid)
        if not msg:
            return Response(status=status.HTTP_404_NOT_FOUND)

        from ..services.triage import trash_message

        try:
            trash_message(msg)
        except Exception as e:
            logger.warning(
                "IMAP move to trash failed for message %s: %s", msg.uuid, scrub(e)
            )
            return Response(
                {"detail": _("Failed to move the message to trash")},
                status=status.HTTP_502_BAD_GATEWAY,
            )
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

        from ..services.imap_connection import shared_connections
        from ..services.imap_messages import set_uids_flag
        from ..services.imap_sync import forget_remote_state
        from ..services.triage import (
            TRASHED,
            archive_message,
            flag_operations,
            mark_not_spam,
            move_to_folder,
            report_spam,
            trash_message,
        )

        # Resolve target folder for move action
        target_folder = None
        if action == "move":
            target_folder_id = ser.validated_data.get("target_folder_id")
            target_folder = (
                user_folders(request.user)
                .select_related("account")
                .filter(uuid=target_folder_id)
                .first()
            )
            if target_folder is None:
                return Response(
                    {"detail": _("Target folder not found")},
                    status=status.HTTP_404_NOT_FOUND,
                )
            # A UUID captured before a merge still addresses the group.
            target_folder = canonical_folder(target_folder)

        # Same table the single-message path uses, keyed by this endpoint's
        # own action names.
        flags = flag_operations()
        action_map = {
            "mark_read": flags["read"],
            "mark_unread": flags["unread"],
            "star": flags["starred"],
            "unstar": flags["unstarred"],
        }

        # Each resolves its own folder per message: a selection of the
        # unified inbox spans accounts, each with its Archive and its Spam.
        filing = {
            "archive": archive_message,
            "spam": report_spam,
            "not_spam": mark_not_spam,
        }

        processed = 0
        synced = True
        refused_folders = set()
        affected_folders = set()
        to_bulk_update = []
        bulk_update_fields = set()
        # A flag goes out as one STORE per mailbox, not one per message.
        uids_by_mailbox = {}
        # One connection per account for the whole batch, opened by the first
        # message that needs it.
        with shared_connections():
            for msg in messages:
                affected_folders.add(msg.folder_id)
                try:
                    if action == "delete":
                        try:
                            # refresh=False: the counters are recomputed once for
                            # the whole batch below.
                            outcome = trash_message(msg, refresh=False)
                        except Exception as e:
                            logger.warning(
                                "IMAP move to trash failed for message %s: %s",
                                msg.uuid,
                                scrub(e),
                            )
                            continue
                        if outcome == TRASHED:
                            affected_folders.add(msg.folder_id)
                    elif action == "move" and target_folder:
                        if target_folder.account_id != msg.account_id:
                            continue
                        # Already in this logical folder: an IMAP COPY + EXPUNGE
                        # here would be a real server round trip for a move the
                        # user sees as "it is already there".
                        if canonical_folder_id(msg.folder) == target_folder.pk:
                            continue
                        try:
                            # refresh=False: the counters are recomputed once for
                            # the whole batch below.
                            move_to_folder(msg, target_folder, refresh=False)
                        except Exception as e:
                            logger.warning(
                                "IMAP move failed for message %s: %s",
                                msg.uuid,
                                scrub(e),
                            )
                            continue
                        # Use .pk to match msg.folder_id added above. refresh_folders_counts_bulk
                        # filters via folder_id__in, so a UUID would never match.
                        affected_folders.add(target_folder.pk)
                    elif action in filing:
                        try:
                            filed = filing[action](msg, refresh=False)
                        except Exception as e:
                            logger.warning(
                                "IMAP %s failed for message %s: %s",
                                scrub(action),
                                msg.uuid,
                                scrub(e),
                            )
                            continue
                        if not filed:
                            continue
                        # The folder the row now points at, or its source
                        # again when it was retired instead.
                        affected_folders.add(msg.folder_id)
                    elif action in action_map:
                        _imap_fn, _imap_flag, field, value = action_map[action]
                        setattr(msg, field, value)
                        bulk_update_fields.add(field)
                        to_bulk_update.append(msg)
                        uids_by_mailbox.setdefault(
                            (msg.account, msg.folder), []
                        ).append(msg.imap_uid)
                    processed += 1
                except Exception:
                    logger.warning(
                        "Batch action '%s' failed for message %s",
                        scrub(action),
                        msg.uuid,
                    )

            if uids_by_mailbox:
                _imap_fn, imap_flag, _field, value = action_map[action]
                for (account, folder), uids in uids_by_mailbox.items():
                    try:
                        set_uids_flag(account, folder, uids, imap_flag, value)
                    except Exception as e:
                        synced = False
                        refused_folders.add(folder.pk)
                        logger.warning(
                            "IMAP %s failed for folder %s: %s",
                            scrub(action),
                            scrub(folder.name),
                            scrub(e),
                        )

        from ..services.counts import refresh_folders_counts_bulk
        from ..services.label_counts import refresh_labels_for_messages

        with transaction.atomic():
            if to_bulk_update:
                MailMessage.objects.bulk_update(
                    to_bulk_update, list(bulk_update_fields)
                )
                forget_remote_state(refused_folders)

            # Refresh counts for all affected folders in a single batch:
            # 1 aggregate + 1 bulk_update instead of 2N queries.
            refresh_folders_counts_bulk(affected_folders)

            # Refresh label counts for read/unread/delete actions
            if action in ("mark_read", "mark_unread", "delete"):
                refresh_labels_for_messages([m.pk for m in messages])

        # Only a flag is applied locally whatever the server answers; a move
        # the server refused is not counted in processed instead.
        if action in action_map:
            return Response({"processed": processed, "synced": synced})
        return Response({"processed": processed})


def _fetch_original(message, headers_only):
    """The server's copy of `message`, or the error Response to send instead."""
    from ..services.imap_messages import fetch_original

    try:
        return fetch_original(message.account, message, headers_only=headers_only)
    except LookupError:
        return Response(
            {"detail": _("The server no longer has this message")},
            status=status.HTTP_404_NOT_FOUND,
        )
    except Exception as e:
        logger.warning(
            "IMAP fetch of the original failed for message %s: %s",
            message.uuid,
            scrub(e),
        )
        return Response(
            {"detail": _("Failed to fetch the message from the server")},
            status=status.HTTP_502_BAD_GATEWAY,
        )


# What a file name may not hold on Windows, macOS or Linux.
_FILENAME_UNSAFE = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]+')


def eml_filename(subject):
    stem = " ".join(_FILENAME_UNSAFE.sub(" ", subject or "").split())
    stem = stem.strip(".")[:100].strip()
    return f"{stem or 'message'}.eml"


@extend_schema(tags=["Mail - Messages"])
class MailMessageOriginalView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Download the original message as .eml",
        responses={(200, "message/rfc822"): bytes},
    )
    def get(self, request, uuid):
        msg = reachable_message(request.user, uuid)
        if msg is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        raw = _fetch_original(msg, headers_only=False)
        if isinstance(raw, Response):
            return raw
        response = HttpResponse(raw, content_type=EML_CONTENT_TYPE)
        response["Content-Disposition"] = content_disposition_header(
            True, eml_filename(msg.subject)
        )
        response["Cache-Control"] = "private, no-store"
        return protect_stored_content(response, EML_CONTENT_TYPE)


@extend_schema(tags=["Mail - Messages"])
class MailMessageHeadersView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get the message's headers as the server stores them",
        responses=inline_serializer(
            "MailMessageHeaders", fields={"headers": serializers.CharField()}
        ),
    )
    def get(self, request, uuid):
        msg = reachable_message(request.user, uuid)
        if msg is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        raw = _fetch_original(msg, headers_only=True)
        if isinstance(raw, Response):
            return raw
        return Response(
            {"headers": raw.decode("utf-8", errors="replace").rstrip("\r\n")},
            headers={"Cache-Control": "private, no-store"},
        )
