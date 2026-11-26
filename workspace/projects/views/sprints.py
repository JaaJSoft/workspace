from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import (
    GenericAPIView,
    ListCreateAPIView,
    RetrieveUpdateDestroyAPIView,
)
from rest_framework.response import Response

from ..models import Project, Sprint, TaskStatus
from ..serializers import SprintCompleteSerializer, SprintSerializer
from ..services.sprints import (
    complete_sprint,
    delete_sprint,
    propagate_sprint_rename,
    start_sprint,
)
from .context import (
    ManagedRowsMixin,
    NamedRowWritesMixin,
    ProjectContextMixin,
    ProjectRowMixin,
)


class _SprintWrites(ManagedRowsMixin, NamedRowWritesMixin):
    queryset = Sprint.objects.none()
    serializer_class = SprintSerializer
    manage_action = "manage_sprints"
    name_taken = gettext_lazy("A sprint with this name already exists in this project.")

    def get_queryset(self):
        # One reverse-FK join serves both rollup counts (epics precedent).
        return self.project.sprints.annotate(
            task_count=Count("tasks"),
            done_task_count=Count(
                "tasks", filter=Q(tasks__status__category=TaskStatus.Category.DONE)
            ),
        ).order_by("created_at", "uuid")

    def require_manage(self):
        super().require_manage()
        # Sprints are the scrum board model; a kanban project only ever
        # holds the closed ones a conversion left behind, as frozen history.
        if self.project.type != Project.Type.SCRUM:
            raise PermissionDenied(_("Sprints require a scrum project."))


class _OneSprintMixin(_SprintWrites, ProjectRowMixin):
    def get_object(self):
        # With its progress counts, which the serializer reads.
        return get_object_or_404(self.get_queryset(), pk=self.row.pk)


@extend_schema(tags=["Projects - Sprints"])
@extend_schema_view(
    get=extend_schema(summary="List the sprints of a project"),
    post=extend_schema(summary="Plan a sprint"),
)
class SprintListView(_SprintWrites, ProjectContextMixin, ListCreateAPIView):
    # A project's sprints, listed whole.
    pagination_class = None


@extend_schema(tags=["Projects - Sprints"])
@extend_schema_view(
    get=extend_schema(summary="Get a sprint"),
    patch=extend_schema(summary="Update a sprint"),
    delete=extend_schema(summary="Delete a sprint"),
)
class SprintDetailView(_OneSprintMixin, RetrieveUpdateDestroyAPIView):
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def perform_update(self, serializer):
        old_name = serializer.instance.name
        super().perform_update(serializer)
        if serializer.instance.name != old_name:
            propagate_sprint_rename(serializer.instance)

    def perform_destroy(self, instance):
        delete_sprint(instance)


@extend_schema(
    tags=["Projects - Sprints"],
    summary="Start a sprint",
    description="Its tasks move onto the board. One sprint runs at a time.",
    request=None,
    responses=SprintSerializer,
)
class SprintStartView(_OneSprintMixin, GenericAPIView):
    def post(self, request, *args, **kwargs):
        start_sprint(self.row, actor=request.user)
        return Response(self.get_serializer(self.get_object()).data)


@extend_schema(
    tags=["Projects - Sprints"],
    summary="Complete the running sprint",
    description=(
        "Unfinished tasks go to the sprint named in `move_to`, or back to "
        "the backlog without one."
    ),
    request=SprintCompleteSerializer,
    responses=SprintSerializer,
)
class SprintCompleteView(_OneSprintMixin, GenericAPIView):
    def post(self, request, *args, **kwargs):
        body = SprintCompleteSerializer(
            data=request.data, context=self.get_serializer_context()
        )
        body.is_valid(raise_exception=True)
        complete_sprint(
            self.row, move_to=body.validated_data["move_to"], actor=request.user
        )
        return Response(self.get_serializer(self.get_object()).data)
