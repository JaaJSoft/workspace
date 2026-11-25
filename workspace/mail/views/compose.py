import logging

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext as _
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.closing import close_all
from workspace.common.logging import scrub

from ..queries import reachable_account, reachable_message
from ..serializers import (
    ComposePrefillSerializer,
    DraftSaveSerializer,
    MailMessageDetailSerializer,
    SendEmailSerializer,
)

logger = logging.getLogger(__name__)


class AttachmentRefused(Exception):
    """An attachment of the request cannot go out; the message is not built."""

    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail

    def response(self):
        return Response({"detail": self.detail}, status=status.HTTP_400_BAD_REQUEST)


def _outgoing_attachments(user, uploads=(), file_uuids=(), attachment_uuids=()):
    """The attachments of an outgoing message, and the handles to close after.

    `file_uuids` name Files nodes, `attachment_uuids` the attachments of the
    user's stored messages. Either list is all-or-nothing: one identifier the
    user cannot reach refuses the message with the answer an unknown one
    gets. Raises AttachmentRefused, with every opened handle closed.
    """
    from workspace.files.queries import resolve_accessible_files

    from ..queries import resolve_reachable_attachments

    ws_files = resolve_accessible_files(user, file_uuids)
    if ws_files is None:
        raise AttachmentRefused(
            _("One or more workspace files not found or not accessible.")
        )
    mail_attachments = resolve_reachable_attachments(user, attachment_uuids)
    if mail_attachments is None:
        raise AttachmentRefused(_("One or more attachments not found."))

    sources = [
        (ws_file.name, ws_file.mime_type, ws_file.content) for ws_file in ws_files
    ] + [(item.filename, item.content_type, item.content) for item in mail_attachments]
    total_size = (
        sum(upload.size for upload in uploads)
        + sum(ws_file.size or 0 for ws_file in ws_files)
        + sum(item.size or 0 for item in mail_attachments)
    )
    if total_size > settings.MAIL_MAX_ATTACHMENTS_SIZE:
        limit_mb = settings.MAIL_MAX_ATTACHMENTS_SIZE / (1024 * 1024)
        raise AttachmentRefused(
            _("Attachments are limited to %(limit)s MB per email.")
            % {"limit": f"{limit_mb:g}"}
        )

    handles = []
    for name, content_type, content in sources:
        try:
            handle = content.open("rb")
        except FileNotFoundError, OSError:
            close_all(handles)
            raise AttachmentRefused(
                _('File "%(name)s" content is unavailable.') % {"name": name}
            ) from None
        handle.name = name
        handle.content_type = content_type
        handles.append(handle)
    return [*uploads, *handles], handles


@extend_schema(tags=["Mail - Messages"])
class MailSendView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Send an email", request=SendEmailSerializer)
    def post(self, request):
        ser = SendEmailSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        account = reachable_account(request.user, d["account_id"])
        if account is None:
            return Response(
                {"detail": _("Account not found")},
                status=status.HTTP_404_NOT_FOUND,
            )

        from ..services.sending import deliver_email

        try:
            attachments, handles = _outgoing_attachments(
                request.user,
                uploads=request.FILES.getlist("attachments", []),
                file_uuids=d["file_uuids"],
                attachment_uuids=d["attachment_uuids"],
            )
        except AttachmentRefused as refusal:
            return refusal.response()

        try:
            # A Sent copy that did not land is not a failed send: deliver_email
            # reports it through `archived`, and the response stays 201.
            delivery = deliver_email(
                account=account,
                to=d["to"],
                subject=d["subject"],
                body_html=d["body_html"],
                body_text=d["body_text"],
                cc=d.get("cc"),
                bcc=d.get("bcc"),
                reply_to=d.get("reply_to"),
                attachments=attachments,
                reply_message_id=d.get("reply_message_id"),
            )
            return Response(
                {"status": "sent", "answered_message_id": delivery.answered},
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            # Use logger.error + scrub(str(e)) instead of logger.exception:
            # the latter would include the raw traceback which can contain
            # un-scrubbed exception text (e.g. an IMAP/SMTP server response
            # with embedded \r\n).
            logger.error(
                "Failed to send email from %s: %s", scrub(account.email), scrub(str(e))
            )
            return Response(
                {"status": "error", "error": _("Failed to send email")},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        finally:
            close_all(handles)


@extend_schema(tags=["Mail - Messages"])
class MailDraftView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Save a draft email", request=DraftSaveSerializer)
    def post(self, request):
        ser = DraftSaveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        account = reachable_account(request.user, d["account_id"])
        if account is None:
            return Response(
                {"detail": _("Account not found")},
                status=status.HTTP_404_NOT_FOUND,
            )

        from ..services.drafts import save_composed_draft

        try:
            attachments, handles = _outgoing_attachments(
                request.user, attachment_uuids=d["attachment_uuids"]
            )
        except AttachmentRefused as refusal:
            return refusal.response()

        try:
            mail_msg = save_composed_draft(
                account,
                to=d.get("to"),
                subject=d.get("subject", ""),
                body_html=d.get("body_html", ""),
                body_text=d.get("body_text", ""),
                cc=d.get("cc"),
                bcc=d.get("bcc"),
                reply_to=d.get("reply_to"),
                attachments=attachments,
                reply_message_id=d.get("reply_message_id"),
                replace_draft_uuid=d.get("draft_id"),
            )
            if mail_msg:
                return Response(
                    MailMessageDetailSerializer(mail_msg).data,
                    status=status.HTTP_201_CREATED,
                )
            return Response(
                {"detail": _("Failed to save draft")},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as e:
            # See comment in MailSendView above re: logger.error vs exception.
            logger.error(
                "Failed to save draft for %s: %s", scrub(account.email), scrub(str(e))
            )
            return Response(
                {"detail": _("Failed to save draft")},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        finally:
            close_all(handles)

    @extend_schema(summary="Delete a draft email")
    def delete(self, request, uuid=None):
        if not uuid:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        msg = reachable_message(request.user, uuid)
        if msg is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        from ..services.imap_messages import delete_draft

        try:
            delete_draft(msg.account, msg)
        except Exception as e:
            logger.warning(
                "Failed to delete draft on IMAP for %s: %s", msg.uuid, scrub(str(e))
            )
            # Fall back to a local soft-delete so the user gets immediate
            # feedback. delete_draft would have set deleted_at after the IMAP
            # call but never reached that line due to the exception, leaving
            # the draft active in DB while the user thinks it was removed.
            # The next sync will reconcile if the server still has the message.
            if msg.deleted_at is None:
                msg.deleted_at = timezone.now()
                msg.save(update_fields=["deleted_at", "updated_at"])

        from ..services.counts import refresh_folder_counts

        refresh_folder_counts(msg.folder)
        return Response(status=status.HTTP_204_NO_CONTENT)


COMPOSE_MODES = ("reply", "reply_all", "forward")


@extend_schema(tags=["Mail - Messages"])
class MailComposePrefillView(APIView):
    """What the compose dialog opens with to answer or forward a message."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Prefill a reply, a reply to all or a forward",
        parameters=[
            OpenApiParameter("mode", enum=COMPOSE_MODES, required=True),
        ],
        responses=ComposePrefillSerializer,
    )
    def get(self, request, uuid):
        from ..services.compose import build_forward, build_reply

        mode = request.query_params.get("mode")
        if mode not in COMPOSE_MODES:
            return Response(
                {"detail": _("Unknown compose mode")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        message = reachable_message(request.user, uuid)
        if message is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if mode == "forward":
            prefill = build_forward(message, request.user)
        else:
            prefill = build_reply(message, request.user, reply_all=mode == "reply_all")
        return Response(
            ComposePrefillSerializer(prefill).data,
            headers={"Cache-Control": "private, no-store"},
        )
