"""Comment-related actions for FileViewSet."""

from django.utils import timezone
from django.utils.translation import gettext as _
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from workspace.common.uuids import UUID_PATTERN
from workspace.files.models import FileComment
from workspace.files.serializers import (
    FileCommentCreateSerializer,
    FileCommentEditSerializer,
    FileCommentSerializer,
)
from workspace.files.services.comments import (
    mentionable_users,
    notify_comment_added,
    notify_comment_edited,
)
from workspace.users.serializers import UserSummarySerializer


def _mention_context(audience):
    return {"mention_map": {u.username: u.pk for u in audience}}


class CommentsMixin:
    """Adds the comments, comment_detail and mentionable_users actions."""

    @extend_schema(
        summary="List the users who can be mentioned on a file",
        description="Everyone who can see it: its owner, its group, who it is shared with.",
        responses=UserSummarySerializer(many=True),
    )
    @action(
        detail=True,
        methods=["get"],
        url_path="mentionable-users",
        pagination_class=None,
        filter_backends=[],
    )
    def mentionable_users(self, request, uuid=None):
        file_obj, perm = self._resolve_file_with_access(uuid)
        return Response(
            UserSummarySerializer(mentionable_users(file_obj), many=True).data
        )

    @extend_schema(
        summary="List or create comments on a file",
        description=(
            "GET lists the comments, oldest first; POST adds one. Who can be "
            "mentioned is at `/files/{uuid}/mentionable-users`."
        ),
        request=FileCommentCreateSerializer,
        responses={
            200: FileCommentSerializer(many=True),
            201: OpenApiResponse(response=FileCommentSerializer),
        },
    )
    @action(
        detail=True,
        methods=["get", "post"],
        url_path="comments",
        pagination_class=None,
        filter_backends=[],
    )
    def comments(self, request, uuid=None):
        """List or create comments on a file/folder."""
        file_obj, perm = self._resolve_file_with_access(uuid)
        audience = mentionable_users(file_obj)

        if request.method == "GET":
            from workspace.notifications.services.notifications import (
                mark_source_read,
            )

            mark_source_read(request.user, file_obj)

            qs = (
                FileComment.objects.filter(
                    file=file_obj,
                    deleted_at__isnull=True,
                )
                .select_related("author")
                .order_by("created_at")
            )
            serializer = FileCommentSerializer(
                qs, many=True, context=_mention_context(audience)
            )
            return Response(serializer.data)

        # POST
        create_ser = FileCommentCreateSerializer(data=request.data)
        create_ser.is_valid(raise_exception=True)
        comment = FileComment.objects.create(
            file=file_obj,
            author=request.user,
            body=create_ser.validated_data["body"],
        )
        notify_comment_added(file_obj, request.user, comment.body, audience=audience)
        serializer = FileCommentSerializer(comment, context=_mention_context(audience))
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Edit or delete a comment",
        description="PATCH to edit, DELETE to soft-delete a comment. Only the author can modify their comment.",
        request=FileCommentEditSerializer,
        responses={
            200: OpenApiResponse(response=FileCommentSerializer),
            204: OpenApiResponse(description="Comment deleted."),
            403: OpenApiResponse(description="Not the comment author."),
            404: OpenApiResponse(description=_("Comment not found.")),
        },
    )
    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path=rf"comments/(?P<comment_uuid>{UUID_PATTERN})",
    )
    def comment_detail(self, request, uuid=None, comment_uuid=None):
        """Edit or soft-delete a comment."""
        file_obj, perm = self._resolve_file_with_access(uuid)

        comment = (
            FileComment.objects.filter(
                uuid=comment_uuid,
                file_id=uuid,
                deleted_at__isnull=True,
            )
            .select_related("author")
            .first()
        )
        if not comment:
            return Response(
                {"detail": _("Comment not found.")}, status=status.HTTP_404_NOT_FOUND
            )

        if comment.author_id != request.user.pk:
            return Response(
                {"detail": _("You can only modify your own comments.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        if request.method == "DELETE":
            comment.deleted_at = timezone.now()
            comment.save(update_fields=["deleted_at"])
            return Response(status=status.HTTP_204_NO_CONTENT)

        # PATCH
        edit_ser = FileCommentEditSerializer(data=request.data)
        edit_ser.is_valid(raise_exception=True)
        old_body = comment.body
        comment.body = edit_ser.validated_data["body"]
        comment.edited_at = timezone.now()
        comment.save(update_fields=["body", "edited_at"])
        audience = mentionable_users(file_obj)
        notify_comment_edited(
            file_obj, request.user, old_body, comment.body, audience=audience
        )
        serializer = FileCommentSerializer(comment, context=_mention_context(audience))
        return Response(serializer.data)
