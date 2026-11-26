from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from workspace.common.http_ranges import serve_with_ranges
from workspace.files.queries import resolve_accessible_files

from ..models import (
    Task,
    TaskAttachment,
    TaskComment,
    TaskFileLink,
    TaskLink,
)
from ..queries import project_users, user_project_ids
from ..serializers import (
    TaskAttachmentCreateSerializer,
    TaskAttachmentSerializer,
    TaskCommentBodySerializer,
    TaskCommentSerializer,
    TaskFileLinkCreateSerializer,
    TaskLinkCreateSerializer,
)
from ..services.attachments import (
    MAX_ATTACHMENTS_PER_REQUEST,
    MAX_UPLOAD_BYTES,
    create_attachments,
    remove_attachment,
)
from ..services.comments import add_comment, notify_comment_edited
from ..services.file_links import file_links_for_task, link_files, unlink_file
from ..services.links import create_link, delete_link, links_for_task
from ..services.members import (
    ProjectRuleError,
)
from .context import ProjectContextMixin


def _rule_error_response(exc):
    """Map a ProjectRuleError to a 400 with its curated detail message."""
    return Response({"detail": exc.detail}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(tags=["Projects - Tasks"])
class TaskLinkViewSet(ProjectContextMixin, viewsets.GenericViewSet):
    """Links anchored on one task: list both directions, create, remove.

    Responses are serialized relative to the anchor task ("blocks" vs "is
    blocked by"); a link whose other end the caller cannot access is hidden
    from the list, never surfaced as a 403.
    """

    serializer_class = TaskLinkCreateSerializer
    lookup_field = "uuid"
    pagination_class = None
    # Schema generation only; list/destroy build their own querysets.
    queryset = TaskLink.objects.none()

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            # select_related caches task.project for the reference snapshots
            # the link events write.
            self.task = self.project.tasks.select_related("project").get(
                uuid=kwargs["task_uuid"]
            )
        except Task.DoesNotExist:
            raise Http404 from None

    def list(self, request, *args, **kwargs):
        return Response(links_for_task(request.user, self.task))

    def create(self, request, *args, **kwargs):
        self._require_action("link", self.task)
        serializer = TaskLinkCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        other = (
            Task.objects.filter(
                uuid=serializer.validated_data["target"],
                project_id__in=user_project_ids(request.user),
                project__archived_at__isnull=True,
            )
            .select_related("project", "status")
            .first()
        )
        if other is None:
            # Unknown and inaccessible targets answer alike (404-not-403).
            return Response(
                {"detail": _("Task not found.")}, status=status.HTTP_404_NOT_FOUND
            )
        try:
            create_link(
                self.task,
                other,
                serializer.validated_data["relation"],
                actor=request.user,
            )
        except ProjectRuleError as exc:
            return _rule_error_response(exc)
        return Response(
            links_for_task(request.user, self.task), status=status.HTTP_201_CREATED
        )

    def destroy(self, request, *args, **kwargs):
        self._require_action("link", self.task)
        link = (
            TaskLink.objects.filter(
                Q(source=self.task) | Q(target=self.task),
                uuid=self.kwargs["uuid"],
            )
            .select_related("source__project", "target__project")
            .first()
        )
        if link is None:
            raise Http404
        delete_link(link, actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Projects - Tasks"])
class TaskCommentViewSet(ProjectContextMixin, viewsets.GenericViewSet):
    serializer_class = TaskCommentSerializer
    lookup_field = "uuid"
    pagination_class = None

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            self.task = self.project.tasks.get(uuid=kwargs["task_uuid"])
        except Task.DoesNotExist:
            raise Http404 from None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return TaskComment.objects.none()
        return (
            self.task.comments.filter(deleted_at__isnull=True)
            .select_related("author")
            .order_by("created_at")
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if getattr(self, "swagger_fake_view", False):
            return context
        context["mention_map"] = {u.username: u.pk for u in self._audience()}
        return context

    def _audience(self):
        if not hasattr(self, "_audience_cache"):
            self._audience_cache = project_users(self.project)
        return self._audience_cache

    def list(self, request, *args, **kwargs):
        from workspace.notifications.services.notifications import mark_source_read

        mark_source_read(request.user, self.task)

        serializer = self.get_serializer(self.get_queryset(), many=True)
        return Response(
            {
                "comments": serializer.data,
                "mention_users": [
                    {
                        "id": u.pk,
                        "username": u.username,
                        "first_name": u.first_name,
                        "last_name": u.last_name,
                    }
                    for u in self._audience()
                ],
            }
        )

    def create(self, request, *args, **kwargs):
        self._require_action("comment", self.task)
        body_ser = TaskCommentBodySerializer(data=request.data)
        body_ser.is_valid(raise_exception=True)
        comment = add_comment(self.task, request.user, body_ser.validated_data["body"])
        return Response(
            self.get_serializer(comment).data, status=status.HTTP_201_CREATED
        )

    def partial_update(self, request, *args, **kwargs):
        self._require_action("comment", self.task)
        comment = self._get_own_comment(request)
        body_ser = TaskCommentBodySerializer(data=request.data)
        body_ser.is_valid(raise_exception=True)
        old_body = comment.body
        comment.body = body_ser.validated_data["body"]
        comment.edited_at = timezone.now()
        comment.save(update_fields=["body", "edited_at"])
        notify_comment_edited(self.task, request.user, old_body, comment.body)
        return Response(self.get_serializer(comment).data)

    def destroy(self, request, *args, **kwargs):
        self._require_action("comment", self.task)
        comment = self._get_own_comment(request)
        comment.deleted_at = timezone.now()
        comment.save(update_fields=["deleted_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _get_own_comment(self, request):
        comment = self.get_queryset().filter(uuid=self.kwargs["uuid"]).first()
        if comment is None:
            raise Http404
        if comment.author_id != request.user.pk:
            raise PermissionDenied(_("You can only modify your own comments."))
        return comment


@extend_schema(tags=["Projects - Tasks"])
class TaskAttachmentViewSet(ProjectContextMixin, viewsets.GenericViewSet):
    serializer_class = TaskAttachmentSerializer
    lookup_field = "uuid"
    pagination_class = None

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            self.task = self.project.tasks.get(uuid=kwargs["task_uuid"])
        except Task.DoesNotExist:
            raise Http404 from None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return TaskAttachment.objects.none()
        return self.task.attachments.select_related("task", "added_by")

    def list(self, request, *args, **kwargs):
        return Response(
            {"attachments": self.get_serializer(self.get_queryset(), many=True).data}
        )

    @extend_schema(request=TaskAttachmentCreateSerializer)
    def create(self, request, *args, **kwargs):
        self._require_action("attach", self.task)
        ser = TaskAttachmentCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        uploads = request.FILES.getlist("files")
        file_uuids = ser.validated_data["file_uuids"]

        if not uploads and not file_uuids:
            return Response(
                {"detail": _("Provide files or file_uuids.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(uploads) + len(file_uuids) > MAX_ATTACHMENTS_PER_REQUEST:
            return Response(
                {
                    "detail": ngettext(
                        "Maximum %(count)s attachment per request.",
                        "Maximum %(count)s attachments per request.",
                        MAX_ATTACHMENTS_PER_REQUEST,
                    )
                    % {"count": MAX_ATTACHMENTS_PER_REQUEST}
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        for f in uploads:
            if f.size > MAX_UPLOAD_BYTES:
                return Response(
                    {
                        "detail": _('File "%(name)s" exceeds the 50 MB limit.')
                        % {"name": f.name}
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        picked = resolve_accessible_files(request.user, file_uuids)
        if picked is None:
            return Response(
                {"detail": _("One or more files not found or not accessible.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                create_attachments(request.user, self.task, uploads, picked)
        except FileNotFoundError, OSError:
            return Response(
                {"detail": _("One or more workspace file contents are unavailable.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {"attachments": self.get_serializer(self.get_queryset(), many=True).data},
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        self._require_action("attach", self.task)
        attachment = self.get_queryset().filter(uuid=kwargs["uuid"]).first()
        if attachment is None:
            raise Http404
        remove_attachment(attachment, request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(summary="Download a task attachment")
    def download(self, request, *args, **kwargs):
        attachment = self.get_queryset().filter(uuid=kwargs["uuid"]).first()
        if attachment is None:
            raise Http404
        try:
            fh = attachment.file.open("rb")
        except FileNotFoundError, OSError:
            raise Http404 from None
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(0)
        return serve_with_ranges(
            request,
            file_handle=fh,
            file_size=size,
            content_type=attachment.mime_type,
            inline_filename=attachment.original_name,
            cache_control="private, max-age=604800, immutable",
        )


@extend_schema(tags=["Projects - Tasks"])
class TaskFileLinkViewSet(ProjectContextMixin, viewsets.GenericViewSet):
    """Workspace files linked to one task: list, link, unlink.

    Linking shares the file with the project, so the list needs no
    per-viewer filtering: every member can open every linked file.
    """

    serializer_class = TaskFileLinkCreateSerializer
    lookup_field = "uuid"
    pagination_class = None
    # Schema generation only; list/destroy build their own querysets.
    queryset = TaskFileLink.objects.none()

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            self.task = self.project.tasks.select_related("project").get(
                uuid=kwargs["task_uuid"]
            )
        except Task.DoesNotExist:
            raise Http404 from None

    def list(self, request, *args, **kwargs):
        return Response({"files": file_links_for_task(self.task)})

    def create(self, request, *args, **kwargs):
        self._require_action("link_file", self.task)
        serializer = TaskFileLinkCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        files = resolve_accessible_files(
            request.user, serializer.validated_data["file_uuids"]
        )
        if files is None:
            return Response(
                {"detail": _("One or more files not found or not accessible.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            link_files(
                request.user,
                self.task,
                files,
                permission=serializer.validated_data["permission"],
            )
        except ProjectRuleError as exc:
            return _rule_error_response(exc)
        return Response(
            {"files": file_links_for_task(self.task)},
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        self._require_action("link_file", self.task)
        link = (
            self.task.file_links.select_related(
                "task", "share__file", "share__shared_with_project"
            )
            .filter(uuid=kwargs["uuid"])
            .first()
        )
        if link is None:
            raise Http404
        unlink_file(link, actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
