from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.permissions import SAFE_METHODS

from workspace.common.views import ReorderView

from ..models import Subtask
from ..serializers import SubtaskSerializer
from ..services.subtasks import create_subtask, reorder_subtasks
from .context import ProjectRowMixin, TaskRowsMixin


class _ChecklistMixin(TaskRowsMixin):
    """A task's checklist: anyone who may edit the task may write it."""

    queryset = Subtask.objects.none()

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.method not in SAFE_METHODS:
            self._require_action("edit", self.task)

    def get_queryset(self):
        return self.task.subtasks.order_by("position", "created_at", "uuid")


@extend_schema(tags=["Projects - Tasks"])
@extend_schema_view(
    get=extend_schema(summary="List the checklist of a task"),
    post=extend_schema(
        summary="Add an item to the checklist of a task",
        description="It joins the end of the checklist.",
    ),
)
class SubtaskListView(_ChecklistMixin, ListCreateAPIView):
    serializer_class = SubtaskSerializer
    # A task's checklist, listed whole.
    pagination_class = None

    def perform_create(self, serializer):
        asked = serializer.validated_data
        serializer.instance = create_subtask(
            self.task, asked["title"], done=asked.get("done", False)
        )


@extend_schema(
    tags=["Projects - Tasks"],
    summary="Order the checklist of a task",
    description="Items the request leaves out keep their order after the ones it lists.",
)
class SubtaskReorderView(_ChecklistMixin, ReorderView):
    def perform_reorder(self, subtasks, data):
        reorder_subtasks(self.task, [subtask.uuid for subtask in subtasks])


@extend_schema(tags=["Projects - Tasks"])
@extend_schema_view(
    get=extend_schema(summary="Get a checklist item"),
    patch=extend_schema(summary="Update a checklist item"),
    delete=extend_schema(summary="Delete a checklist item"),
)
class SubtaskDetailView(ProjectRowMixin, RetrieveUpdateDestroyAPIView):
    queryset = Subtask.objects.none()
    serializer_class = SubtaskSerializer
    http_method_names = ["get", "patch", "delete", "head", "options"]
    project_path = "task__project"

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.method not in SAFE_METHODS:
            self._require_action("edit", self.row.task)

    def changed_task_uuid(self):
        return self.row.task_id
