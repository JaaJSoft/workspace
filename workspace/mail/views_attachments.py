import logging

from django.http import FileResponse
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.uuids import parse_uuid_or_none
from .models import MailAttachment

logger = logging.getLogger(__name__)


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
            ).get(uuid=uuid)
        except MailAttachment.DoesNotExist:
            return Response(
                {'detail': 'Attachment not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if attachment.message.account.owner != request.user:
            return Response(status=status.HTTP_404_NOT_FOUND)

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
                parent = File.objects.get(
                    uuid=folder_uuid,
                    owner=request.user,
                    node_type=File.NodeType.FOLDER,
                    deleted_at__isnull=True,
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
        # The try/except is intentionally narrow: only a missing source blob
        # is mapped to 404. Operational errors from FileService.create_file
        # (disk full / perm denied / remote storage flake on the destination
        # side) propagate so middleware returns 500 - they're not "attachment
        # not found" and lying to the client would mask the real issue.
        try:
            src = attachment.content.open('rb')
        except FileNotFoundError:
            return Response(
                {'detail': 'Attachment not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        with src as f:
            file_obj = FileService.create_file(
                owner=request.user,
                name=attachment.filename,
                parent=parent,
                content=DjangoFile(f, name=attachment.filename),
                mime_type=attachment.content_type,
            )

        return Response(
            {'detail': 'File saved.', 'file_uuid': str(file_obj.uuid)},
            status=status.HTTP_201_CREATED,
        )
