import logging

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.http_ranges import serve_with_ranges
from workspace.common.logging import scrub
from workspace.common.uuids import parse_uuid_or_none
from .models import MailAttachment
from .queries import user_account_ids

logger = logging.getLogger(__name__)


@extend_schema(tags=['Mail'])
class MailAttachmentDownloadView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Download an attachment")
    def get(self, request, uuid):
        try:
            attachment = MailAttachment.objects.select_related(
                'message__account',
            ).get(
                uuid=uuid,
                message__account_id__in=user_account_ids(request.user),
            )
        except MailAttachment.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        # A vanished blob (storage cleanup, migration, manual deletion) would
        # otherwise propagate as a bare 500. 404 is more truthful and mirrors
        # MailAttachmentSaveToFilesView below.
        try:
            src = attachment.content.open('rb')
        except FileNotFoundError:
            return Response(status=status.HTTP_404_NOT_FOUND)

        size = attachment.size or getattr(src, 'size', 0)
        if not size:
            src.seek(0, 2)
            size = src.tell()
            src.seek(0)
        # Range support lets download managers resume an interrupted save and
        # lets the browser stream a previewable attachment (PDF, video, etc.).
        return serve_with_ranges(
            request,
            file_handle=src,
            file_size=size,
            content_type=attachment.content_type,
            attachment_filename=attachment.filename,
            cache_control='private, max-age=2592000, immutable',
        )


@extend_schema(tags=['Mail'])
class MailAttachmentSaveToFilesView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Save a mail attachment to the user's Files",
        request=inline_serializer('MailAttachmentSaveToFiles', fields={
            'folder_id': serializers.UUIDField(required=False, help_text='Target folder UUID.'),
        }),
    )
    def post(self, request, uuid):
        from django.core.files.base import File as DjangoFile
        from workspace.files.models import File
        from workspace.files.services.files import FileService

        try:
            attachment = MailAttachment.objects.select_related(
                'message__account',
            ).get(
                uuid=uuid,
                message__account_id__in=user_account_ids(request.user),
            )
        except MailAttachment.DoesNotExist:
            return Response(
                {'detail': 'Attachment not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Resolve target folder
        parent = None
        folder_id = request.data.get('folder_id')
        if folder_id:
            folder_uuid = parse_uuid_or_none(folder_id)
            if folder_uuid is None:
                return Response(
                    {'detail': '"folder_id" must be a valid UUID.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                parent = FileService.user_files_qs(request.user).get(
                    uuid=folder_uuid,
                    node_type=File.NodeType.FOLDER,
                )
            except File.DoesNotExist:
                return Response(
                    {'detail': 'Folder not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

        # Stream-copy the attachment blob into a fresh storage path. Wrapping
        # the opened FieldFile in django.core.files.File flips _committed=False
        # so the destination FileField sees the file as new and storage.save()
        # runs (a FieldFile passed directly is _committed=True and would make
        # both rows point at the same blob).
        #
        # FileNotFoundError on either side means the source blob is gone -
        # mapped to 404 ("Attachment not found"). Other OSError variants
        # (disk full / perm denied / remote storage flake on the destination
        # side) get a single sanitized log line with the attachment path
        # before being re-raised, so middleware turns them into 500 with a
        # breadcrumb instead of a bare stack trace.
        try:
            src = attachment.content.open('rb')
            with src as f:
                file_obj = FileService.create_file(
                    owner=request.user,
                    name=attachment.filename,
                    parent=parent,
                    content=DjangoFile(f, name=attachment.filename),
                    mime_type=attachment.content_type,
                    acting_user=request.user,
                )
        except FileNotFoundError:
            return Response(
                {'detail': 'Attachment not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except OSError:
            logger.exception(
                "Failed to save mail attachment %s to files",
                scrub(attachment.content.name or attachment.filename),
            )
            raise

        return Response(
            {'detail': 'File saved.', 'file_uuid': str(file_obj.uuid)},
            status=status.HTTP_201_CREATED,
        )
