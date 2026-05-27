"""Bulk actions endpoint + shared-with-me + AI edit."""

import logging

from django.conf import settings
from django.db.models import Exists, OuterRef, Subquery
from django.http import Http404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from workspace.files.models import File, FileFavorite, FileShare, PinnedFolder
from workspace.files.serializers import FileSerializer
from workspace.files.services import FileService

logger = logging.getLogger(__name__)


class ActionsMixin:
    """Adds files_actions, shared_with_me, ai_edit actions."""

    @extend_schema(
        summary="Get available actions for files/folders",
        description=(
            "Return available actions for a list of file/folder UUIDs. "
            "Returns a map keyed by UUID, each value being the list of "
            "available actions for that item."
        ),
        request={
            'application/json': {
                'type': 'object',
                'properties': {
                    'uuids': {
                        'type': 'array',
                        'items': {'type': 'string', 'format': 'uuid'},
                        'description': 'List of file/folder UUIDs',
                    },
                },
                'required': ['uuids'],
            },
        },
        responses={
            200: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Map of UUID to list of available actions.",
            ),
            400: OpenApiResponse(description="Invalid request."),
            404: OpenApiResponse(description="One or more UUIDs not found."),
        },
    )
    @action(detail=False, methods=['post'], url_path='actions')
    def files_actions(self, request):
        """Return available actions per file/folder for a set of UUIDs."""
        from workspace.files.actions import ActionRegistry

        uuids = request.data.get('uuids', [])
        if not isinstance(uuids, list) or len(uuids) == 0:
            return Response(
                {'detail': 'uuids must be a non-empty list.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(uuids) > 200:
            return Response(
                {'detail': 'Too many UUIDs (max 200).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        favorite_subquery = FileFavorite.objects.filter(
            owner=request.user,
            file_id=OuterRef('pk'),
        )
        pinned_subquery = PinnedFolder.objects.filter(
            owner=request.user,
            folder_id=OuterRef('pk'),
        )
        user_share_subquery = FileShare.objects.filter(
            file_id=OuterRef('pk'),
            shared_with=request.user,
        ).values('permission')[:1]

        file_objects = list(
            File.objects.filter(
                FileService.accessible_files_q(request.user),
                uuid__in=uuids,
            ).annotate(
                is_favorite=Exists(favorite_subquery),
                is_pinned=Exists(pinned_subquery),
                user_share_permission=Subquery(user_share_subquery),
            ).distinct()
        )

        if len(file_objects) != len(set(uuids)):
            return Response(
                {'detail': 'One or more UUIDs not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        result = {}
        for file_obj in file_objects:
            perm = FileService.get_permission(request.user, file_obj)
            result[str(file_obj.uuid)] = ActionRegistry.get_available_actions(
                request.user, file_obj, permission=perm,
            )
        return Response(result)

    @extend_schema(
        summary="Files shared with me",
        description="Return files that have been shared with the current user.",
        responses={
            200: OpenApiResponse(
                response=FileSerializer(many=True),
                description="List of shared files.",
            ),
        },
    )
    @action(detail=False, methods=['get'], url_path='shared-with-me')
    def shared_with_me(self, request):
        """List files shared with the current user."""
        shared_file_ids = FileShare.objects.filter(
            shared_with=request.user,
        ).values_list('file_id', flat=True)
        queryset = File.objects.filter(
            pk__in=shared_file_ids,
            node_type=File.NodeType.FILE,
            deleted_at__isnull=True,
        )
        queryset = FileService.annotate_for_serializer(queryset, request.user).annotate(
            user_share_permission=Subquery(
                FileShare.objects.filter(
                    file_id=OuterRef('pk'),
                    shared_with=request.user,
                ).values('permission')[:1]
            ),
        ).name_ordered()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="AI-edit an image file",
        request={
            'application/json': {
                'type': 'object',
                'properties': {
                    'prompt': {'type': 'string', 'description': 'Edit instruction'},
                    'size': {'type': 'string', 'enum': ['1024x1024', '1792x1024', '1024x1792']},
                    'source_image': {'type': 'string', 'nullable': True, 'description': 'Base64 source (null = use original file)'},
                },
                'required': ['prompt'],
            }
        },
        responses={
            200: OpenApiResponse(description="Base64-encoded edited image."),
            400: OpenApiResponse(description="Missing prompt."),
            404: OpenApiResponse(description="File not found or AI not configured."),
            502: OpenApiResponse(description="AI backend error."),
        },
    )
    @action(detail=True, methods=['post'], url_path='ai-edit')
    def ai_edit(self, request, uuid=None):
        """Edit an image file using AI based on a text prompt."""
        import base64
        import binascii

        if not getattr(settings, 'AI_IMAGE_MODEL', ''):
            raise Http404

        file_obj = self.get_object()

        from workspace.files.services.filetype import get_group
        if file_obj.node_type != File.NodeType.FILE or get_group(file_obj.type or '') != 'image':
            return Response({'error': 'file is not an image'}, status=status.HTTP_400_BAD_REQUEST)

        prompt = request.data.get('prompt', '').strip()
        if not prompt:
            return Response({'error': 'prompt is required'}, status=status.HTTP_400_BAD_REQUEST)

        size = request.data.get('size', '1024x1024')

        # Use provided source_image (iterative edit) or read from storage
        source_b64 = request.data.get('source_image', None)
        if source_b64 is not None:
            # Treat an explicit empty string as a 400 instead of falling
            # through to the stored file - that path silently mutated the
            # wrong source on iterative-edit requests.
            if isinstance(source_b64, str) and not source_b64.strip():
                return Response({'error': 'source_image cannot be empty'}, status=status.HTTP_400_BAD_REQUEST)
            try:
                # validate=True rejects characters outside the base64 alphabet
                # and bad padding instead of silently producing garbage bytes
                # that would otherwise be sent to the paid AI service.
                source_data = base64.b64decode(source_b64, validate=True)
            except (binascii.Error, ValueError, TypeError):
                return Response({'error': 'invalid base64 in source_image'}, status=status.HTTP_400_BAD_REQUEST)
        else:
            try:
                # Context manager guarantees the handle closes even when
                # read() raises mid-stream.
                with file_obj.content.open('rb') as fh:
                    source_data = fh.read()
            except Exception:
                return Response({'error': 'could not read file content'}, status=status.HTTP_400_BAD_REQUEST)

        from workspace.ai.services.image import ai_edit_image
        try:
            image_data = ai_edit_image(source_data, prompt, size)
        except ValueError:
            return Response({'error': 'invalid request (check prompt and AI configuration)'}, status=status.HTTP_400_BAD_REQUEST)
        except RuntimeError:
            logger.exception('AI image edit failed')
            return Response({'error': 'image editing failed'}, status=status.HTTP_502_BAD_GATEWAY)

        return Response({'image': base64.b64encode(image_data).decode()})
