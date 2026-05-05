import logging

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import MailAccount, MailFolder, MailMessage
from .serializers import (
    DraftSaveSerializer,
    MailMessageDetailSerializer,
    SendEmailSerializer,
)

logger = logging.getLogger(__name__)


@extend_schema(tags=['Mail'])
class MailSendView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Send an email", request=SendEmailSerializer)
    def post(self, request):
        ser = SendEmailSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        try:
            account = MailAccount.objects.get(uuid=d['account_id'], owner=request.user)
        except MailAccount.DoesNotExist:
            return Response(
                {'detail': 'Account not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        from .services.smtp import send_email

        attachments = request.FILES.getlist('attachments', [])

        try:
            raw_msg = send_email(
                account=account,
                to=d['to'],
                subject=d['subject'],
                body_html=d['body_html'],
                body_text=d['body_text'],
                cc=d.get('cc'),
                bcc=d.get('bcc'),
                reply_to=d.get('reply_to'),
                attachments=attachments,
            )

            # Copy to Sent folder via IMAP APPEND, then sync the Sent folder
            from .services.imap_messages import append_to_sent
            from .services.imap_sync import sync_folder_messages
            try:
                append_to_sent(account, raw_msg)
            except Exception:
                logger.warning("Failed to append sent message to IMAP for %s", account.email)

            try:
                sent_folder = MailFolder.objects.filter(
                    account=account, folder_type='sent',
                ).first()
                if sent_folder:
                    sync_folder_messages(account, sent_folder)
            except Exception:
                logger.warning("Failed to sync sent folder after send for %s", account.email)

            return Response({'status': 'sent'}, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.exception("Failed to send email from %s", account.email)
            return Response(
                {'status': 'error', 'error': 'Failed to send email'},
                status=status.HTTP_502_BAD_GATEWAY,
            )


@extend_schema(tags=['Mail'])
class MailDraftView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Save a draft email", request=DraftSaveSerializer)
    def post(self, request):
        ser = DraftSaveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        try:
            account = MailAccount.objects.get(uuid=d['account_id'], owner=request.user)
        except MailAccount.DoesNotExist:
            return Response(
                {'detail': 'Account not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        from .services.smtp import build_draft_message

        raw_msg = build_draft_message(
            account,
            to=d.get('to'),
            subject=d.get('subject', ''),
            body_html=d.get('body_html', ''),
            body_text=d.get('body_text', ''),
            cc=d.get('cc'),
            bcc=d.get('bcc'),
            reply_to=d.get('reply_to'),
        )

        # If updating an existing draft, find the old IMAP UID
        old_uid = None
        if d.get('draft_id'):
            try:
                old_msg = MailMessage.objects.get(
                    uuid=d['draft_id'], account=account, deleted_at__isnull=True,
                )
                old_uid = old_msg.imap_uid
            except MailMessage.DoesNotExist:
                pass

        from .services.imap_messages import save_draft

        try:
            mail_msg = save_draft(account, raw_msg, old_uid=old_uid)
            if mail_msg:
                return Response(
                    MailMessageDetailSerializer(mail_msg).data,
                    status=status.HTTP_201_CREATED,
                )
            return Response(
                {'detail': 'Failed to save draft'},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as e:
            logger.exception("Failed to save draft for %s", account.email)
            return Response(
                {'detail': 'Failed to save draft'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

    @extend_schema(summary="Delete a draft email")
    def delete(self, request, uuid=None):
        if not uuid:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        try:
            msg = MailMessage.objects.select_related('account', 'folder').get(
                uuid=uuid, deleted_at__isnull=True,
            )
        except MailMessage.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if msg.account.owner != request.user:
            return Response(status=status.HTTP_404_NOT_FOUND)

        from .services.imap_messages import delete_draft

        try:
            delete_draft(msg.account, msg)
        except Exception as e:
            logger.warning("Failed to delete draft on IMAP for %s: %s", msg.uuid, e)
            # Fall back to a local soft-delete so the user gets immediate
            # feedback. delete_draft would have set deleted_at after the IMAP
            # call but never reached that line due to the exception, leaving
            # the draft active in DB while the user thinks it was removed.
            # The next sync will reconcile if the server still has the message.
            if msg.deleted_at is None:
                msg.deleted_at = timezone.now()
                msg.save(update_fields=['deleted_at', 'updated_at'])

        from .views import _refresh_folder_counts
        _refresh_folder_counts(msg.folder)
        return Response(status=status.HTTP_204_NO_CONTENT)
