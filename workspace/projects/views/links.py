"""What a task points at: other tasks, and files of the workspace."""

from django.http import Http404
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import DestroyAPIView, GenericAPIView, ListCreateAPIView
from rest_framework.response import Response

from workspace.files.queries import resolve_accessible_files

from ..models import TaskFileLink, TaskLink
from ..queries import get_project_role
from ..serializers import (
    TaskFileLinkCreateSerializer,
    TaskFileLinkSerializer,
    TaskLinkSerializer,
)
from ..services.file_links import file_links_for_task, link_files, unlink_file
from ..services.links import create_link, delete_link, link_seen_from, links_for_task
from .context import ProjectContextMixin, ProjectRowMixin, TaskRowsMixin


@extend_schema(tags=["Projects - Tasks"])
@extend_schema_view(
    get=extend_schema(
        summary="List the links of a task",
        description=(
            "Both directions, each as this task sees it: `blocks` here is "
            "`blocked_by` on the other end. A link to a task of a project "
            "the caller cannot open is left out."
        ),
        responses=TaskLinkSerializer(many=True),
    ),
    post=extend_schema(summary="Link a task to another"),
)
class TaskLinkListView(TaskRowsMixin, GenericAPIView):
    queryset = TaskLink.objects.none()
    serializer_class = TaskLinkSerializer
    pagination_class = None

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["task"] = self.task
        return context

    def get(self, request, *args, **kwargs):
        links = links_for_task(request.user, self.task)
        return Response(self.get_serializer(links, many=True).data)

    def post(self, request, *args, **kwargs):
        self._require_action("link", self.task)
        body = self.get_serializer(data=request.data)
        body.is_valid(raise_exception=True)
        link = create_link(
            self.task,
            body.validated_data["task"],
            body.validated_data["relation"],
            actor=request.user,
        )
        return Response(
            self.get_serializer(link_seen_from(link, self.task)).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(
    tags=["Projects - Tasks"],
    summary="Remove a link between two tasks",
    description="From either end: whoever may link one of the two tasks.",
)
class TaskLinkDetailView(ProjectContextMixin, GenericAPIView):
    queryset = TaskLink.objects.none()

    def resolve_project(self, uuid, **kwargs):
        link = (
            TaskLink.objects.filter(uuid=uuid)
            .select_related("source__project", "target__project")
            .first()
        )
        if link is None:
            raise Http404
        self.link = link
        reachable = [
            task
            for task in (link.source, link.target)
            if get_project_role(self.request.user, task.project) is not None
        ]
        if not reachable:
            raise Http404
        # Acted on from an end whose project still takes changes, if any.
        self.task = next(
            (task for task in reachable if not task.project.is_archived), reachable[0]
        )
        return self.task.project

    def changed_task_uuid(self):
        return self.task.pk

    def delete(self, request, *args, **kwargs):
        self._require_action("link", self.task)
        delete_link(self.link, actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Projects - Tasks"])
@extend_schema_view(
    get=extend_schema(summary="List the workspace files linked to a task"),
    post=extend_schema(
        summary="Link workspace files to a task",
        description=(
            "Each file is shared with the project, so every member can open "
            "it; `permission` is the one of a share this creates. A file "
            "already linked is left as it is. Answers the links it created."
        ),
        request=TaskFileLinkCreateSerializer,
        responses={201: TaskFileLinkSerializer(many=True)},
    ),
)
class FileLinkListView(TaskRowsMixin, ListCreateAPIView):
    queryset = TaskFileLink.objects.none()
    serializer_class = TaskFileLinkSerializer
    # A task's linked files, listed whole.
    pagination_class = None

    def get_queryset(self):
        return file_links_for_task(self.task)

    def create(self, request, *args, **kwargs):
        self._require_action("link_file", self.task)
        body = TaskFileLinkCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        files = resolve_accessible_files(request.user, body.validated_data["files"])
        if files is None:
            raise ValidationError(
                {"files": [_("One or more files not found or not accessible.")]}
            )
        created = link_files(
            request.user,
            self.task,
            files,
            permission=body.validated_data["permission"],
        )
        return Response(
            self.get_serializer(created, many=True).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(
    tags=["Projects - Tasks"],
    summary="Unlink a workspace file from its task",
    description="A share the link created goes with its last link.",
)
class FileLinkDetailView(ProjectRowMixin, DestroyAPIView):
    queryset = TaskFileLink.objects.none()
    serializer_class = TaskFileLinkSerializer
    project_path = "task__project"

    def rows(self):
        return TaskFileLink.objects.select_related(
            "share__file", "share__shared_with_project"
        )

    def changed_task_uuid(self):
        return self.row.task_id

    def destroy(self, request, *args, **kwargs):
        self._require_action("link_file", self.row.task)
        unlink_file(self.row, actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
