from django.utils import timezone
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.permissions import SAFE_METHODS

from workspace.notifications.services.notifications import mark_source_read

from ..models import TaskComment
from ..queries import project_users
from ..serializers import TaskCommentSerializer
from ..services.comments import add_comment, notify_comment_edited
from .context import ProjectRowMixin, TaskRowsMixin


class _MentionsMixin:
    """A comment is rendered with the mentions of who the project reaches."""

    queryset = TaskComment.objects.none()
    serializer_class = TaskCommentSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["mention_map"] = {
            user.username: user.pk for user in project_users(self.project)
        }
        return context


@extend_schema(tags=["Projects - Tasks"])
@extend_schema_view(
    get=extend_schema(
        summary="List the comments of a task",
        description=(
            "Oldest first. Reading them marks the caller's notifications "
            "about the task as read. Who can be mentioned is the list of "
            "`/projects/{project_uuid}/users`."
        ),
    ),
    post=extend_schema(summary="Comment on a task"),
)
class CommentListView(_MentionsMixin, TaskRowsMixin, ListCreateAPIView):
    def get_queryset(self):
        return (
            self.task.comments.filter(deleted_at__isnull=True)
            .select_related("author")
            .order_by("created_at", "uuid")
        )

    def list(self, request, *args, **kwargs):
        mark_source_read(request.user, self.task)
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        self._require_action("comment", self.task)
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.instance = add_comment(
            self.task, self.request.user, serializer.validated_data["body"]
        )


@extend_schema(tags=["Projects - Tasks"])
@extend_schema_view(
    get=extend_schema(summary="Get a comment"),
    patch=extend_schema(summary="Edit a comment", description="Its author alone can."),
    delete=extend_schema(
        summary="Delete a comment", description="Its author alone can."
    ),
)
class CommentDetailView(_MentionsMixin, ProjectRowMixin, RetrieveUpdateDestroyAPIView):
    http_method_names = ["get", "patch", "delete", "head", "options"]
    project_path = "task__project"

    def rows(self):
        return TaskComment.objects.filter(deleted_at__isnull=True).select_related(
            "author"
        )

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.method in SAFE_METHODS:
            return
        self._require_action("comment", self.row.task)
        if self.row.author_id != request.user.pk:
            raise PermissionDenied(_("You can only modify your own comments."))

    def changed_task_uuid(self):
        return self.row.task_id

    def perform_update(self, serializer):
        comment = serializer.instance
        old_body = comment.body
        comment.body = serializer.validated_data.get("body", old_body)
        comment.edited_at = timezone.now()
        comment.save(update_fields=["body", "edited_at"])
        notify_comment_edited(comment.task, self.request.user, old_body, comment.body)

    def perform_destroy(self, comment):
        comment.deleted_at = timezone.now()
        comment.save(update_fields=["deleted_at"])
