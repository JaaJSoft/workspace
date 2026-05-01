"""Comment-related actions for FileViewSet."""

from django.contrib.auth import get_user_model
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from workspace.files.models import FileComment
from workspace.files.serializers import (
    FileCommentCreateSerializer,
    FileCommentEditSerializer,
    FileCommentSerializer,
)
from workspace.notifications.services.notifications import notify_many

User = get_user_model()


class CommentsMixin:
    """Adds comments and comment_detail actions."""

    @extend_schema(
        summary="List or create comments on a file",
        description="GET to list comments, POST to add a new comment.",
        request=FileCommentCreateSerializer,
        responses={
            200: OpenApiResponse(response=FileCommentSerializer(many=True)),
            201: OpenApiResponse(response=FileCommentSerializer),
        },
    )
    @action(detail=True, methods=['get', 'post'], url_path='comments')
    def comments(self, request, uuid=None):
        """List or create comments on a file/folder."""
        file_obj, perm = self._resolve_file_with_access(uuid)

        if request.method == 'GET':
            qs = FileComment.objects.filter(
                file=file_obj,
                deleted_at__isnull=True,
            ).select_related('author').order_by('created_at')
            serializer = FileCommentSerializer(qs, many=True)
            return Response(serializer.data)

        # POST
        create_ser = FileCommentCreateSerializer(data=request.data)
        create_ser.is_valid(raise_exception=True)
        comment = FileComment.objects.create(
            file=file_obj,
            author=request.user,
            body=create_ser.validated_data['body'],
        )
        recipients = set()
        if file_obj.owner != request.user:
            recipients.add(file_obj.owner)
        commenter_ids = FileComment.objects.filter(
            file=file_obj, deleted_at__isnull=True,
        ).exclude(author=request.user).values_list('author', flat=True).distinct()
        recipients.update(User.objects.filter(pk__in=commenter_ids))
        if recipients:
            notify_many(
                recipients=list(recipients),
                origin='files',
                title=f'{request.user.username} commented on "{file_obj.name}"',
                url=f'/files/{file_obj.parent_id}' if file_obj.parent_id else '/files',
                actor=request.user,
            )
        serializer = FileCommentSerializer(comment)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Edit or delete a comment",
        description="PATCH to edit, DELETE to soft-delete a comment. Only the author can modify their comment.",
        request=FileCommentEditSerializer,
        responses={
            200: OpenApiResponse(response=FileCommentSerializer),
            204: OpenApiResponse(description="Comment deleted."),
            403: OpenApiResponse(description="Not the comment author."),
            404: OpenApiResponse(description="Comment not found."),
        },
    )
    @action(
        detail=True,
        methods=['patch', 'delete'],
        url_path=r'comments/(?P<comment_uuid>[0-9a-f-]+)',
    )
    def comment_detail(self, request, uuid=None, comment_uuid=None):
        """Edit or soft-delete a comment."""
        self._resolve_file_with_access(uuid)

        comment = FileComment.objects.filter(
            uuid=comment_uuid,
            file_id=uuid,
            deleted_at__isnull=True,
        ).select_related('author').first()
        if not comment:
            return Response({'detail': 'Comment not found.'}, status=status.HTTP_404_NOT_FOUND)

        if comment.author_id != request.user.pk:
            return Response({'detail': 'You can only modify your own comments.'}, status=status.HTTP_403_FORBIDDEN)

        if request.method == 'DELETE':
            comment.deleted_at = timezone.now()
            comment.save(update_fields=['deleted_at'])
            return Response(status=status.HTTP_204_NO_CONTENT)

        # PATCH
        edit_ser = FileCommentEditSerializer(data=request.data)
        edit_ser.is_valid(raise_exception=True)
        comment.body = edit_ser.validated_data['body']
        comment.edited_at = timezone.now()
        comment.save(update_fields=['body', 'edited_at'])
        serializer = FileCommentSerializer(comment)
        return Response(serializer.data)
