import logging

from django.core.files.storage import default_storage
from django.http import FileResponse
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.cache import cached, invalidate_tags
from workspace.common.uuids import parse_uuid_or_none
from .models import MessageAttachment
from .services.conversations import get_active_membership

logger = logging.getLogger(__name__)


@cached(
    key=lambda attachment_id: f'att:meta:{attachment_id}',
    ttl=60,
    tags=lambda attachment_id: [f'att:{attachment_id}'],
)
def _get_attachment_meta_db(attachment_id):
    """Fetch immutable attachment metadata. Cached per attachment, not per user.

    Returns the raw row dict (or None). Callers must check authorisation
    separately - membership is mutable and must NOT be memoised here.
    """
    return (
        MessageAttachment.objects
        .filter(uuid=attachment_id)
        .values('file', 'mime_type', 'original_name', 'message__conversation_id')
        .first()
    )


def _get_attachment_meta(user, attachment_id):
    """Return ``{file, mime, name}`` for an attachment the user can access, else None.

    Both "not found" and "not a member" collapse into ``None`` - callers return 404
    in both cases so we don't leak which attachments exist to outsiders.
    """
    row = _get_attachment_meta_db(attachment_id)
    if row is None or not get_active_membership(user, row['message__conversation_id']):
        return None
    return {
        'file': row['file'],
        'mime': row['mime_type'],
        'name': row['original_name'],
    }


@extend_schema(tags=['Chat'])
class AttachmentDownloadView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Download a chat attachment")
    def get(self, request, attachment_id):
        meta = _get_attachment_meta(request.user, attachment_id)
        if meta is None:
            return Response(
                {'detail': 'Attachment not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            fh = default_storage.open(meta['file'], 'rb')
        except (FileNotFoundError, OSError):
            invalidate_tags(f'att:{attachment_id}')
            return Response(
                {'detail': 'Attachment not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        response = FileResponse(fh, content_type=meta['mime'])
        # Sanitize filename for Content-Disposition header
        safe_name = meta['name'].replace('"', '\\"').replace('\n', '').replace('\r', '')
        response['Content-Disposition'] = f'inline; filename="{safe_name}"'
        response['Cache-Control'] = 'private, max-age=604800, immutable'
        return response


@extend_schema(tags=['Chat'])
class AttachmentSaveToFilesView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Save a chat attachment to the user's Files",
        request=inline_serializer('AttachmentSaveToFiles', fields={
            'folder_id': serializers.UUIDField(required=False, help_text='Target folder UUID.'),
        }),
    )
    def post(self, request, attachment_id):
        from django.core.files.base import File as DjangoFile
        from workspace.files.services.files import FileService

        try:
            attachment = (
                MessageAttachment.objects
                .select_related('message')
                .get(uuid=attachment_id)
            )
        except MessageAttachment.DoesNotExist:
            return Response(
                {'detail': 'Attachment not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        membership = get_active_membership(
            request.user, attachment.message.conversation_id,
        )
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        from workspace.files.models import File

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

        # Stream-copy the source blob into a fresh storage path. We wrap
        # the opened FieldFile in django.core.files.File so the destination
        # FileField sees _committed=False and storage.save() runs (FieldFile
        # itself is committed and would be reused as-is, leaving the two
        # rows pointing at the same blob). FileFound/OSError mirrors the
        # download view's handling for a vanished blob.
        try:
            with attachment.file.open('rb') as f:
                file_obj = FileService.create_file(
                    owner=request.user,
                    name=attachment.original_name,
                    parent=parent,
                    content=DjangoFile(f, name=attachment.original_name),
                    mime_type=attachment.mime_type,
                    acting_user=request.user,
                )
        except (FileNotFoundError, OSError):
            return Response(
                {'detail': 'Attachment not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {'detail': 'File saved.', 'file_uuid': str(file_obj.uuid)},
            status=status.HTTP_201_CREATED,
        )
