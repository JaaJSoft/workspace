import logging
from collections import Counter, defaultdict

from django.db.models import Count, Q
from django.http import FileResponse
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import MailAccount, MailAttachment, MailFolder, MailMessage
from .serializers import (
    BatchActionSerializer,
    DraftSaveSerializer,
    MailAccountCreateSerializer,
    MailAccountSerializer,
    MailAccountUpdateSerializer,
    MailFolderCreateSerializer,
    MailFolderSerializer,
    MailFolderUpdateSerializer,
    MailMessageDetailSerializer,
    MailMessageListSerializer,
    MailMessageUpdateSerializer,
    SendEmailSerializer,
)

logger = logging.getLogger(__name__)


def _refresh_folder_counts(folder):
    """Recompute message_count and unread_count for a folder."""
    qs = MailMessage.objects.filter(folder=folder, deleted_at__isnull=True)
    folder.message_count = qs.count()
    folder.unread_count = qs.filter(is_read=False).count()
    folder.save(update_fields=['message_count', 'unread_count', 'updated_at'])


@extend_schema(tags=['Mail'])
class MailAutodiscoverView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Auto-discover IMAP/SMTP settings for an email address")
    def post(self, request):
        email = (request.data.get('email') or '').strip()
        if not email or '@' not in email:
            return Response(
                {'detail': 'A valid email address is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        domain = email.split('@', 1)[1]

        from myldiscovery import autodiscover

        try:
            settings = autodiscover(domain)
        except Exception:
            logger.info("Autodiscover failed for domain %s", domain)
            settings = None

        if not settings or not settings.get('imap') or not settings.get('smtp'):
            return Response(
                {'detail': 'Could not auto-detect settings for this domain'},
                status=status.HTTP_404_NOT_FOUND,
            )

        imap = settings['imap']
        smtp = settings['smtp']

        # Map starttls to use_ssl / use_tls
        imap_use_ssl = not imap.get('starttls', False)
        smtp_use_tls = smtp.get('starttls', True)

        return Response({
            'imap_host': imap.get('server', ''),
            'imap_port': imap.get('port', 993),
            'imap_use_ssl': imap_use_ssl,
            'smtp_host': smtp.get('server', ''),
            'smtp_port': smtp.get('port', 587),
            'smtp_use_tls': smtp_use_tls,
        })


@extend_schema(tags=['Mail'])
class MailAccountListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="List user's mail accounts")
    def get(self, request):
        accounts = MailAccount.objects.filter(owner=request.user)
        return Response(MailAccountSerializer(accounts, many=True).data)

    @extend_schema(summary="Add a mail account", request=MailAccountCreateSerializer)
    def post(self, request):
        ser = MailAccountCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        password = d.pop('password')
        account = MailAccount(owner=request.user, **d)
        account.set_password(password)
        account.save()

        return Response(
            MailAccountSerializer(account).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=['Mail'])
class MailAccountDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_account(self, request, uuid):
        try:
            return MailAccount.objects.get(uuid=uuid, owner=request.user)
        except MailAccount.DoesNotExist:
            return None

    @extend_schema(summary="Get mail account details")
    def get(self, request, uuid):
        account = self._get_account(request, uuid)
        if not account:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(MailAccountSerializer(account).data)

    @extend_schema(summary="Update a mail account", request=MailAccountUpdateSerializer)
    def patch(self, request, uuid):
        account = self._get_account(request, uuid)
        if not account:
            return Response(status=status.HTTP_404_NOT_FOUND)

        ser = MailAccountUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        password = ser.validated_data.pop('password', None)
        for key, value in ser.validated_data.items():
            setattr(account, key, value)
        if password:
            account.set_password(password)
        account.save()

        return Response(MailAccountSerializer(account).data)

    @extend_schema(summary="Delete a mail account")
    def delete(self, request, uuid):
        account = self._get_account(request, uuid)
        if not account:
            return Response(status=status.HTTP_404_NOT_FOUND)
        account.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['Mail'])
class MailAccountTestView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Test IMAP and SMTP connections for an account")
    def post(self, request, uuid):
        try:
            account = MailAccount.objects.get(uuid=uuid, owner=request.user)
        except MailAccount.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        from .services.imap import test_imap_connection
        from .services.smtp import test_smtp_connection

        imap_ok, imap_error = test_imap_connection(account)
        smtp_ok, smtp_error = test_smtp_connection(account)

        if imap_error:
            logger.warning("IMAP test failed for %s: %s", account.email, imap_error)
        if smtp_error:
            logger.warning("SMTP test failed for %s: %s", account.email, smtp_error)

        return Response({
            'imap': {'success': imap_ok, 'error': 'Connection failed' if imap_error else None},
            'smtp': {'success': smtp_ok, 'error': 'Connection failed' if smtp_error else None},
        })


@extend_schema(tags=['Mail'])
class MailAccountSyncView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Trigger sync for a mail account")
    def post(self, request, uuid):
        try:
            account = MailAccount.objects.get(uuid=uuid, owner=request.user)
        except MailAccount.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        from .services.imap import sync_account

        try:
            sync_account(account)
            return Response({'status': 'ok', 'last_sync_at': account.last_sync_at})
        except Exception as e:
            account.last_sync_error = str(e)
            account.save(update_fields=['last_sync_error', 'updated_at'])
            logger.exception("Failed to sync account %s", account.email)
            return Response(
                {'status': 'error', 'error': 'Sync failed'},
                status=status.HTTP_502_BAD_GATEWAY,
            )


@extend_schema(tags=['Mail'])
class MailFolderListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="List folders for a mail account",
        parameters=[OpenApiParameter('account', str, required=True)],
    )
    def get(self, request):
        account_id = request.query_params.get('account')
        if not account_id:
            return Response(
                {'detail': 'account query parameter is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            account = MailAccount.objects.get(uuid=account_id, owner=request.user)
        except MailAccount.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        folders = MailFolder.objects.filter(account=account)
        return Response(MailFolderSerializer(folders, many=True).data)

    @extend_schema(summary="Create a folder", request=MailFolderCreateSerializer)
    def post(self, request):
        ser = MailFolderCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        try:
            account = MailAccount.objects.get(
                uuid=ser.validated_data['account_id'], owner=request.user,
            )
        except MailAccount.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        from .services.imap import create_folder

        try:
            folder = create_folder(
                account,
                ser.validated_data['name'],
                parent_name=ser.validated_data.get('parent_name', ''),
            )
            return Response(
                MailFolderSerializer(folder).data,
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            logger.warning("Failed to create folder for %s: %s", account.email, e)
            return Response(
                {'detail': 'Failed to create folder'},
                status=status.HTTP_502_BAD_GATEWAY,
            )


@extend_schema(tags=['Mail'])
class MailFolderUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_folder(self, request, uuid):
        try:
            folder = MailFolder.objects.select_related('account').get(uuid=uuid)
        except MailFolder.DoesNotExist:
            return None
        if folder.account.owner != request.user:
            return None
        return folder

    @extend_schema(summary="Update folder (icon, color, rename)")
    def patch(self, request, uuid):
        folder = self._get_folder(request, uuid)
        if not folder:
            return Response(status=status.HTTP_404_NOT_FOUND)

        ser = MailFolderUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        # Rename on IMAP if display_name changed
        new_display_name = ser.validated_data.pop('display_name', None)
        if new_display_name and new_display_name != folder.display_name:
            from .services.imap import rename_folder

            try:
                rename_folder(folder.account, folder, new_display_name)
            except Exception as e:
                logger.warning("Failed to rename folder for %s: %s", folder.account.email, e)
                return Response(
                    {'detail': 'Failed to rename folder'},
                    status=status.HTTP_502_BAD_GATEWAY,
                )

        # Update icon/color locally
        update_fields = ['updated_at']
        for field in ('icon', 'color'):
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
        if folder.folder_type != 'other':
            return Response(
                {'detail': 'Cannot delete a special folder'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from .services.imap import delete_folder

        try:
            delete_folder(folder.account, folder)
        except Exception as e:
            logger.warning("Failed to delete folder for %s: %s", folder.account.email, e)
            return Response(
                {'detail': 'Failed to delete folder'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # Clear selection if this folder was selected
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['Mail'])
class MailFolderMarkReadView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Mark all messages in a folder as read")
    def post(self, request, uuid):
        try:
            folder = MailFolder.objects.select_related('account').get(uuid=uuid)
        except MailFolder.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if folder.account.owner != request.user:
            return Response(status=status.HTTP_404_NOT_FOUND)

        updated = MailMessage.objects.filter(
            folder=folder, is_read=False, deleted_at__isnull=True,
        ).update(is_read=True)

        folder.unread_count = 0
        folder.save(update_fields=['unread_count', 'updated_at'])

        return Response({'updated': updated})


@extend_schema(tags=['Mail'])
class MailMessageListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="List messages in a folder",
        parameters=[
            OpenApiParameter('folder', str, required=True),
            OpenApiParameter('page', int, required=False),
            OpenApiParameter('search', str, required=False),
            OpenApiParameter('unread', bool, required=False),
            OpenApiParameter('starred', bool, required=False),
            OpenApiParameter('attachments', bool, required=False),
        ],
    )
    def get(self, request):
        folder_id = request.query_params.get('folder')
        if not folder_id:
            return Response(
                {'detail': 'folder query parameter is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            folder = MailFolder.objects.select_related('account').get(uuid=folder_id)
        except MailFolder.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if folder.account.owner != request.user:
            return Response(status=status.HTTP_404_NOT_FOUND)

        page = int(request.query_params.get('page', 1))
        page_size = 50
        offset = (page - 1) * page_size

        qs = MailMessage.objects.filter(folder=folder, deleted_at__isnull=True)

        # Apply optional filters
        search = request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(
                Q(subject__icontains=search)
                | Q(snippet__icontains=search)
                | Q(from_address__icontains=search)
            )
        if request.query_params.get('unread'):
            qs = qs.filter(is_read=False)
        if request.query_params.get('starred'):
            qs = qs.filter(is_starred=True)
        if request.query_params.get('attachments'):
            qs = qs.filter(has_attachments=True)

        total = qs.count()
        messages = (
            qs.annotate(attachments_count=Count('attachments'))
            .order_by('-date')[offset:offset + page_size]
        )

        return Response({
            'results': MailMessageListSerializer(messages, many=True).data,
            'count': total,
            'page': page,
            'page_size': page_size,
        })


@extend_schema(tags=['Mail'])
class MailMessageDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_message(self, request, uuid):
        try:
            msg = (
                MailMessage.objects
                .select_related('account', 'folder')
                .prefetch_related('attachments')
                .get(uuid=uuid)
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

        from .services.imap import mark_read, mark_unread, star_message, unstar_message

        if 'is_read' in ser.validated_data:
            val = ser.validated_data['is_read']
            msg.is_read = val
            try:
                if val:
                    mark_read(msg.account, msg)
                else:
                    mark_unread(msg.account, msg)
            except Exception:
                logger.warning("Failed to sync read flag to IMAP for %s", msg.uuid)

        if 'is_starred' in ser.validated_data:
            val = ser.validated_data['is_starred']
            msg.is_starred = val
            try:
                if val:
                    star_message(msg.account, msg)
                else:
                    unstar_message(msg.account, msg)
            except Exception:
                logger.warning("Failed to sync star flag to IMAP for %s", msg.uuid)

        msg.save()
        _refresh_folder_counts(msg.folder)
        return Response(MailMessageDetailSerializer(msg).data)

    @extend_schema(summary="Soft-delete a message")
    def delete(self, request, uuid):
        msg = self._get_message(request, uuid)
        if not msg:
            return Response(status=status.HTTP_404_NOT_FOUND)

        from .services.imap import delete_message

        msg.deleted_at = timezone.now()
        msg.save(update_fields=['deleted_at', 'updated_at'])

        try:
            delete_message(msg.account, msg)
        except Exception:
            logger.warning("Failed to delete message on IMAP for %s", msg.uuid)

        _refresh_folder_counts(msg.folder)
        return Response(status=status.HTTP_204_NO_CONTENT)


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
            from .services.imap import append_to_sent, sync_folder_messages
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
class MailBatchActionView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Batch action on messages", request=BatchActionSerializer)
    def post(self, request):
        ser = BatchActionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        message_ids = ser.validated_data['message_ids']
        action = ser.validated_data['action']

        messages = MailMessage.objects.filter(
            uuid__in=message_ids,
            account__owner=request.user,
            deleted_at__isnull=True,
        ).select_related('account', 'folder')

        from .services.imap import (
            delete_message,
            mark_read,
            mark_unread,
            move_message,
            star_message,
            unstar_message,
        )

        # Resolve target folder for move action
        target_folder = None
        if action == 'move':
            target_folder_id = ser.validated_data.get('target_folder_id')
            try:
                target_folder = MailFolder.objects.select_related('account').get(uuid=target_folder_id)
            except MailFolder.DoesNotExist:
                return Response(
                    {'detail': 'Target folder not found'},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if target_folder.account.owner != request.user:
                return Response(status=status.HTTP_404_NOT_FOUND)

        action_map = {
            'mark_read': (mark_read, {'is_read': True}),
            'mark_unread': (mark_unread, {'is_read': False}),
            'star': (star_message, {'is_starred': True}),
            'unstar': (unstar_message, {'is_starred': False}),
        }

        processed = 0
        affected_folders = set()
        for msg in messages:
            affected_folders.add(msg.folder_id)
            try:
                if action == 'delete':
                    msg.deleted_at = timezone.now()
                    msg.save(update_fields=['deleted_at', 'updated_at'])
                    try:
                        delete_message(msg.account, msg)
                    except Exception:
                        pass
                elif action == 'move' and target_folder:
                    if target_folder.account_id != msg.account_id:
                        continue
                    try:
                        move_message(msg.account, msg, target_folder)
                    except Exception:
                        logger.warning("IMAP move failed for message %s", msg.uuid)
                    msg.folder = target_folder
                    msg.save(update_fields=['folder', 'updated_at'])
                    affected_folders.add(target_folder.uuid)
                elif action in action_map:
                    imap_fn, db_update = action_map[action]
                    for key, value in db_update.items():
                        setattr(msg, key, value)
                    msg.save()
                    try:
                        imap_fn(msg.account, msg)
                    except Exception:
                        pass
                processed += 1
            except Exception:
                logger.warning("Batch action '%s' failed for message %s", action, msg.uuid)

        # Refresh counts for all affected folders
        for folder in MailFolder.objects.filter(uuid__in=affected_folders):
            _refresh_folder_counts(folder)

        return Response({'processed': processed})


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

        from .services.imap import save_draft

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

        from .services.imap import delete_draft

        try:
            delete_draft(msg.account, msg)
        except Exception:
            logger.warning("Failed to delete draft on IMAP for %s", msg.uuid)

        _refresh_folder_counts(msg.folder)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['Mail'])
class MailAttachmentDownloadView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Download an attachment")
    def get(self, request, uuid):
        try:
            attachment = MailAttachment.objects.select_related(
                'message__account',
            ).get(uuid=uuid)
        except MailAttachment.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if attachment.message.account.owner != request.user:
            return Response(status=status.HTTP_404_NOT_FOUND)

        return FileResponse(
            attachment.content.open('rb'),
            content_type=attachment.content_type,
            as_attachment=True,
            filename=attachment.filename,
        )


@extend_schema(tags=['Mail'])
class ContactAutocompleteView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Autocomplete contacts from message history",
        parameters=[
            OpenApiParameter('q', str, required=True, description='Search query (min 2 chars)'),
            OpenApiParameter('account_id', str, required=False, description='Filter by account'),
        ],
    )
    def get(self, request):
        q = (request.query_params.get('q') or '').strip()
        if len(q) < 2:
            return Response([])

        account_filter = Q(account__owner=request.user)
        account_id = request.query_params.get('account_id')
        if account_id:
            account_filter &= Q(account__uuid=account_id)

        q_lower = q.lower()

        messages = (
            MailMessage.objects
            .filter(account_filter, deleted_at__isnull=True)
            .filter(
                Q(from_address__icontains=q)
                | Q(to_addresses__icontains=q)
                | Q(cc_addresses__icontains=q)
            )
            .order_by('-date')
            .only('from_address', 'to_addresses', 'cc_addresses')[:500]
        )

        # Extract all addresses and count frequency
        email_count = Counter()
        email_names = defaultdict(Counter)

        for msg in messages:
            addresses = []
            fa = msg.from_address
            if isinstance(fa, dict) and fa.get('email'):
                addresses.append(fa)
            for field in (msg.to_addresses, msg.cc_addresses):
                if isinstance(field, list):
                    addresses.extend(
                        a for a in field if isinstance(a, dict) and a.get('email')
                    )

            for addr in addresses:
                email = addr['email'].strip().lower()
                name = (addr.get('name') or '').strip()
                # Post-filter: check that the query actually matches this contact
                if q_lower not in email and q_lower not in name.lower():
                    continue
                email_count[email] += 1
                if name:
                    email_names[email][name] += 1

        # Build results sorted by frequency
        results = []
        for email, count in email_count.most_common(15):
            name_counter = email_names.get(email)
            name = name_counter.most_common(1)[0][0] if name_counter else ''
            results.append({'name': name, 'email': email, 'count': count})

        return Response(results)
