from django.http import Http404
from django.utils.translation import gettext as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import (
    GenericAPIView,
    ListCreateAPIView,
    RetrieveDestroyAPIView,
)
from rest_framework.response import Response

from workspace.common.http_ranges import serve_with_ranges
from workspace.files.queries import resolve_accessible_files

from ..models import TaskAttachment
from ..serializers import TaskAttachmentCreateSerializer, TaskAttachmentSerializer
from ..services.attachments import create_attachments, remove_attachment
from .context import ProjectRowMixin, TaskRowsMixin


@extend_schema(tags=["Projects - Tasks"])
@extend_schema_view(
    get=extend_schema(summary="List the attachments of a task"),
    post=extend_schema(
        summary="Attach files to a task",
        description=(
            "Uploads go in `file`, repeated for several, as "
            "`multipart/form-data`; `files` names workspace files, each "
            "copied onto the task. Answers the attachments it created."
        ),
        request={
            "multipart/form-data": TaskAttachmentCreateSerializer,
            "application/json": TaskAttachmentCreateSerializer,
        },
        responses={201: TaskAttachmentSerializer(many=True)},
    ),
)
class AttachmentListView(TaskRowsMixin, ListCreateAPIView):
    queryset = TaskAttachment.objects.none()
    serializer_class = TaskAttachmentSerializer
    # A task's attachments, listed whole.
    pagination_class = None

    def get_queryset(self):
        return self.task.attachments.select_related("added_by").order_by(
            "created_at", "uuid"
        )

    def create(self, request, *args, **kwargs):
        self._require_action("attach", self.task)
        body = TaskAttachmentCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        picked = resolve_accessible_files(request.user, body.validated_data["files"])
        if picked is None:
            raise ValidationError(
                {"files": [_("One or more files not found or not accessible.")]}
            )
        try:
            created = create_attachments(
                request.user, self.task, body.validated_data["file"], picked
            )
        except FileNotFoundError, OSError:
            raise ValidationError(
                {"files": [_("One or more workspace file contents are unavailable.")]}
            ) from None
        return Response(
            self.get_serializer(created, many=True).data,
            status=status.HTTP_201_CREATED,
        )


class _OneAttachmentMixin(ProjectRowMixin):
    queryset = TaskAttachment.objects.none()
    serializer_class = TaskAttachmentSerializer
    project_path = "task__project"

    def rows(self):
        return TaskAttachment.objects.select_related("added_by")

    def changed_task_uuid(self):
        return self.row.task_id


@extend_schema(tags=["Projects - Tasks"])
@extend_schema_view(
    get=extend_schema(summary="Get an attachment"),
    delete=extend_schema(summary="Remove an attachment from its task"),
)
class AttachmentDetailView(_OneAttachmentMixin, RetrieveDestroyAPIView):
    def destroy(self, request, *args, **kwargs):
        self._require_action("attach", self.row.task)
        remove_attachment(self.row, request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class _AttachmentBytesView(_OneAttachmentMixin, GenericAPIView):
    # Whether the browser shows the bytes or saves them.
    inline = True

    def get(self, request, *args, **kwargs):
        attachment = self.row
        try:
            handle = attachment.file.open("rb")
        except FileNotFoundError, OSError:
            raise Http404 from None
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(0)
        disposition = "inline_filename" if self.inline else "attachment_filename"
        return serve_with_ranges(
            request,
            file_handle=handle,
            file_size=size,
            content_type=attachment.mime_type,
            cache_control="private, max-age=604800, immutable",
            **{disposition: attachment.original_name},
        )


_BYTES = {(200, "application/octet-stream"): OpenApiTypes.BINARY}


@extend_schema(
    tags=["Projects - Tasks"],
    summary="Read the bytes of an attachment",
    description="Served inline, with range requests.",
    responses=_BYTES,
)
class AttachmentContentView(_AttachmentBytesView):
    inline = True


@extend_schema(
    tags=["Projects - Tasks"],
    summary="Download an attachment",
    description="Served as a file to save, with range requests.",
    responses=_BYTES,
)
class AttachmentDownloadView(_AttachmentBytesView):
    inline = False
