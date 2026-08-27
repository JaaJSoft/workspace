import logging

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.closing import close_all
from workspace.common.logging import scrub

from .models import MailAccount, MailMessage
from .serializers import (
    DraftSaveSerializer,
    MailMessageDetailSerializer,
    SendEmailSerializer,
)

logger = logging.getLogger(__name__)


@extend_schema(tags=["Mail - Messages"])
class MailSendView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Send an email", request=SendEmailSerializer)
    def post(self, request):
        ser = SendEmailSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        try:
            account = MailAccount.objects.get(uuid=d["account_id"], owner=request.user)
        except MailAccount.DoesNotExist:
            return Response(
                {"detail": "Account not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        from .services.sending import deliver_email

        attachments = list(request.FILES.getlist("attachments", []))

        file_uuids = d.get("file_uuids", [])
        ws_file_handles = []
        if file_uuids:
            from workspace.files.services.files import FileService

            ws_files = FileService.resolve_accessible_files(request.user, file_uuids)
            if ws_files is None:
                return Response(
                    {
                        "detail": "One or more workspace files not found or not accessible."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            for ws_file in ws_files:
                try:
                    handle = ws_file.content.open("rb")
                    handle.name = ws_file.name
                    ws_file_handles.append(handle)
                    attachments.append(handle)
                except FileNotFoundError, OSError:
                    close_all(ws_file_handles)
                    return Response(
                        {"detail": f'File "{ws_file.name}" content is unavailable.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        try:
            # A Sent copy that did not land is not a failed send: deliver_email
            # reports it through `archived`, and the response stays 201.
            deliver_email(
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
            return Response({"status": "sent"}, status=status.HTTP_201_CREATED)
        except Exception as e:
            # Use logger.error + scrub(str(e)) instead of logger.exception:
            # the latter would include the raw traceback which can contain
            # un-scrubbed exception text (e.g. an IMAP/SMTP server response
            # with embedded \r\n).
            logger.error(
                "Failed to send email from %s: %s", scrub(account.email), scrub(str(e))
            )
            return Response(
                {"status": "error", "error": "Failed to send email"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        finally:
            close_all(ws_file_handles)


@extend_schema(tags=["Mail - Messages"])
class MailDraftView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Save a draft email", request=DraftSaveSerializer)
    def post(self, request):
        ser = DraftSaveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        try:
            account = MailAccount.objects.get(uuid=d["account_id"], owner=request.user)
        except MailAccount.DoesNotExist:
            return Response(
                {"detail": "Account not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        from .services.drafts import save_composed_draft

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
                reply_message_id=d.get("reply_message_id"),
                replace_draft_uuid=d.get("draft_id"),
            )
            if mail_msg:
                return Response(
                    MailMessageDetailSerializer(mail_msg).data,
                    status=status.HTTP_201_CREATED,
                )
            return Response(
                {"detail": "Failed to save draft"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as e:
            # See comment in MailSendView above re: logger.error vs exception.
            logger.error(
                "Failed to save draft for %s: %s", scrub(account.email), scrub(str(e))
            )
            return Response(
                {"detail": "Failed to save draft"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

    @extend_schema(summary="Delete a draft email")
    def delete(self, request, uuid=None):
        if not uuid:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        try:
            msg = MailMessage.objects.select_related("account", "folder").get(
                uuid=uuid,
                deleted_at__isnull=True,
            )
        except MailMessage.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if msg.account.owner != request.user:
            return Response(status=status.HTTP_404_NOT_FOUND)

        from .services.imap_messages import delete_draft

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

        from .services.counts import refresh_folder_counts

        refresh_folder_counts(msg.folder)
        return Response(status=status.HTTP_204_NO_CONTENT)
