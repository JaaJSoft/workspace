from django.http import Http404
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import (
    ListAPIView,
    ListCreateAPIView,
    RetrieveUpdateDestroyAPIView,
)
from rest_framework.response import Response

from workspace.common.exceptions import Conflict
from workspace.common.views import BatchView, LinkView, ReorderView, unknown_rows

from ..filters import AllTasksFilter, TaskListFilter
from ..models import Task, TaskEvent
from ..queries import visible_tasks
from ..serializers import (
    TaskBatchEditSerializer,
    TaskBatchMoveSerializer,
    TaskBatchSprintSerializer,
    TaskReorderSerializer,
    TaskSerializer,
    TaskWatchSerializer,
)
from ..services.assignments import notify_assigned
from ..services.bulk import UNSET, bulk_update_tasks, delete_tasks
from ..services.estimates import format_estimate
from ..services.events import record_task_event
from ..services.members import ProjectRuleError
from ..services.sprints import assign_tasks_to_sprint
from ..services.tasks import (
    change_task_status,
    create_task,
    delete_task,
    has_field_updates,
    move_tasks,
    reorder_tasks,
    settle_moved_due_dates,
)
from ..services.watchers import auto_watch, clear_watch_state, set_watch_state
from .viewsets import ProjectContextMixin


def _with_relations(tasks):
    """*tasks* with what ``TaskSerializer`` reads of each, in a fixed number
    of queries."""
    return tasks.select_related(
        "project", "status", "epic", "sprint", "created_by"
    ).prefetch_related("assignees", "labels")


class TaskLotMixin:
    """For the views acting on several tasks of a project at once."""

    def get_queryset(self):
        return self.project.tasks.all()

    def refuse_if_gone(self, tasks):
        """Refuse the request when one of *tasks* was deleted since it was read.

        Called once the service has run, inside the request's transaction:
        the service holds its locks by then, so what is left cannot change
        anymore, and a service skips a task it does not find instead of
        failing. Raising undoes what it did to the others.
        """
        left = set(
            self.project.tasks.filter(pk__in=[task.pk for task in tasks]).values_list(
                "pk", flat=True
            )
        )
        gone = [task.pk for task in tasks if task.pk not in left]
        if gone:
            raise unknown_rows(gone)


class TaskContextMixin(ProjectContextMixin):
    """The project and the caller's role, read from the task the path names.

    A task is addressed without its project (``/projects/tasks/{uuid}``): the
    task says which project it is in. One of a project the caller cannot
    reach answers like one that does not exist.
    """

    def resolve_project(self, uuid, **kwargs):
        try:
            self.task = _with_relations(Task.objects.all()).get(uuid=uuid)
        except Task.DoesNotExist:
            raise Http404 from None
        return self.task.project

    def changed_task_uuid(self):
        return self.kwargs["uuid"]

    def get_object(self):
        return self.task


@extend_schema(tags=["Projects - Tasks"])
@extend_schema_view(
    get=extend_schema(summary="List the tasks of a project"),
    post=extend_schema(summary="Create a task"),
)
class TaskListView(ProjectContextMixin, ListCreateAPIView):
    queryset = Task.objects.none()
    serializer_class = TaskSerializer
    filterset_class = TaskListFilter

    def get_queryset(self):
        return _with_relations(self.project.tasks).order_by(
            "position", "created_at", "uuid"
        )

    def create(self, request, *args, **kwargs):
        self._require_writable()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = create_task(self.project, request.user, **serializer.validated_data)
        return Response(self.get_serializer(task).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Projects - Tasks"])
@extend_schema_view(
    get=extend_schema(
        operation_id="api_v1_projects_all_tasks_list",
        summary="List the tasks of every project the caller can open",
        description=(
            "Most recently updated first; searched with `q`, best match "
            "first. Tasks of archived projects are left out unless "
            "`include_archived` is true."
        ),
    )
)
class AllTasksView(ListAPIView):
    queryset = Task.objects.none()
    serializer_class = TaskSerializer
    filterset_class = AllTasksFilter

    def get_queryset(self):
        return _with_relations(visible_tasks(self.request.user)).order_by(
            "-updated_at", "uuid"
        )


@extend_schema(tags=["Projects - Tasks"])
@extend_schema_view(
    get=extend_schema(summary="Get a task"),
    patch=extend_schema(summary="Update a task"),
    delete=extend_schema(summary="Delete a task"),
)
class TaskDetailView(TaskContextMixin, RetrieveUpdateDestroyAPIView):
    queryset = Task.objects.none()
    serializer_class = TaskSerializer
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def partial_update(self, request, *args, **kwargs):
        self._require_task_edit(request.data)
        return super().partial_update(request, *args, **kwargs)

    def perform_update(self, serializer):
        old_due_date = serializer.instance.due_date
        old_estimate = serializer.instance.estimate
        old_epic = serializer.instance.epic
        old_sprint = serializer.instance.sprint
        old_assignee_ids = {u.pk for u in serializer.instance.assignees.all()}
        # Compared before save: afterwards the instance already carries the
        # new values and every edit would look like a no-op.
        fields_updated = has_field_updates(
            serializer.instance, serializer.validated_data
        )
        # The status is not the serializer's to write: it moves together with
        # the position and completed_at, in change_task_status's locked write.
        new_status = serializer.validated_data.pop("status", None)
        task = serializer.save()
        # Recorded before the status move: a task entering a board column
        # joins the running sprint there, and change_task_status records that
        # join, which must come after the change the request asked for.
        saved_sprint = task.sprint
        if saved_sprint != old_sprint:
            # Sprint names snapshotted, same rationale as the epic names.
            record_task_event(
                task,
                type=TaskEvent.Type.SPRINT,
                actor=self.request.user,
                from_value=old_sprint.name if old_sprint else "",
                to_value=saved_sprint.name if saved_sprint else "",
                from_ref=old_sprint.pk if old_sprint else None,
                to_ref=saved_sprint.pk if saved_sprint else None,
            )
        if new_status is not None:
            # Even when it names the status loaded above: another writer may
            # have moved the task since, and the client asked for this one.
            change_task_status(
                task,
                new_status,
                actor=self.request.user,
                # A sprint named in the same request is the client's choice.
                keep_sprint=serializer.validated_data.get("sprint") is not None,
            )
        added_assignees = [
            u
            for u in serializer.validated_data.get("assignees", [])
            if u.pk not in old_assignee_ids
        ]
        if added_assignees:
            record_task_event(
                task, type=TaskEvent.Type.ASSIGNED, actor=self.request.user
            )
            auto_watch(task, added_assignees)
            notify_assigned(task, self.request.user, added_assignees)
        if fields_updated:
            record_task_event(
                task, type=TaskEvent.Type.UPDATED, actor=self.request.user
            )
        if task.estimate != old_estimate:
            record_task_event(
                task,
                type=TaskEvent.Type.ESTIMATED,
                actor=self.request.user,
                from_value=format_estimate(old_estimate),
                to_value=format_estimate(task.estimate),
            )
        if task.epic_id != (old_epic.pk if old_epic else None):
            # Epic *names* snapshotted, same rationale as the status names:
            # epics are renamable and deletable, a FK would rewrite history.
            record_task_event(
                task,
                type=TaskEvent.Type.EPIC,
                actor=self.request.user,
                from_value=old_epic.name if old_epic else "",
                to_value=task.epic.name if task.epic else "",
            )
        if task.due_date != old_due_date:
            settle_moved_due_dates([task])
        # The response describes the row as stored, moves landed by another
        # writer since the task was loaded included.
        try:
            task.refresh_from_db(
                from_queryset=Task.objects.select_related("status", "sprint"),
                fields=["status", "position", "completed_at", "sprint", "updated_at"],
            )
        except Task.DoesNotExist:
            # Deleted meanwhile: answer the 404 an unknown task gets.
            raise Http404 from None

    def destroy(self, request, *args, **kwargs):
        self._require_action("delete")
        return super().destroy(request, *args, **kwargs)

    def perform_destroy(self, instance):
        delete_task(instance, actor=self.request.user)


@extend_schema(tags=["Projects - Tasks"], summary="Apply one action to several tasks")
class TaskBatchView(TaskLotMixin, ProjectContextMixin, BatchView):
    """What the selection toolbars of a backlog and a task list do.

    ``move`` appends the tasks to a column, ``set_sprint`` plans them into a
    sprint or returns them to the pool, ``edit`` assigns, labels, prioritises
    or dates them, ``delete`` deletes them. Each is set-oriented and answers
    the tasks it changed, so replaying a request changes nothing more and
    answers an empty list.
    """

    batch_name = "Task"
    batch_actions = {
        "move": TaskBatchMoveSerializer,
        "set_sprint": TaskBatchSprintSerializer,
        "edit": TaskBatchEditSerializer,
        "delete": None,
    }
    queryset = Task.objects.none()
    serializer_class = TaskSerializer

    def _changed(self, tasks, changed):
        """The tasks the service changed, as stored, in the order asked."""
        self.refuse_if_gone(tasks)
        stored = _with_relations(self.project.tasks).in_bulk([t.pk for t in changed])
        return [stored[task.pk] for task in tasks if task.pk in stored]

    def perform_move(self, tasks, data):
        self._require_action("move")
        moved = move_tasks(
            self.project,
            data["status"],
            [task.uuid for task in tasks],
            actor=self.request.user,
        )
        return self._changed(tasks, moved)

    def perform_set_sprint(self, tasks, data):
        self._require_action("move")
        try:
            planned = assign_tasks_to_sprint(
                self.project,
                data["sprint"],
                [task.uuid for task in tasks],
                actor=self.request.user,
            )
        except ProjectRuleError as exc:
            raise Conflict(exc.detail) from exc
        return self._changed(tasks, planned)

    def perform_edit(self, tasks, data):
        self._require_bulk_edit(data)
        due_date = data.get("due_date")
        starting_later = [
            task
            for task in tasks
            if due_date is not None
            and task.start_date is not None
            and task.start_date > due_date
        ]
        if starting_later:
            raise self._due_date_refused(starting_later)
        edited, skipped = bulk_update_tasks(
            self.project,
            [task.uuid for task in tasks],
            actor=self.request.user,
            assign=data.get("assign", ()),
            unassign=data.get("unassign", ()),
            add_labels=data.get("add_labels", ()),
            remove_labels=data.get("remove_labels", ()),
            priority=data.get("priority"),
            due_date=data["due_date"] if "due_date" in data else UNSET,
        )
        if skipped:
            # A start date moved past the due date after the check above: the
            # service left that task alone under its lock.
            later = self.project.tasks.filter(
                pk__in=[task.pk for task in tasks], start_date__gt=due_date
            )
            raise self._due_date_refused(later)
        return self._changed(tasks, edited)

    @staticmethod
    def _due_date_refused(tasks):
        """Under the field that is refused, not under ``uuids``: the tasks
        exist, and a client tells the two refusals apart by where they sit."""
        return ValidationError(
            {
                "data": {
                    "due_date": [
                        _("%(task)s starts after this due date.") % {"task": task.uuid}
                        for task in tasks
                    ]
                }
            }
        )

    def perform_delete(self, tasks, data):
        self._require_action("delete")
        deleted = delete_tasks(
            self.project, [task.uuid for task in tasks], actor=self.request.user
        )
        if deleted != len(tasks):
            # Deleted by someone else since the lot was read. Which ones can
            # no longer be told apart from the ones this request deleted, and
            # raising brings every one of those back.
            raise unknown_rows([task.pk for task in tasks])


@extend_schema(
    tags=["Projects - Tasks"],
    summary="Order the tasks of a column",
    description=(
        "One drag-and-drop endpoint: the backlog order, the order within a "
        "column, and a drop into another column, whose tasks move there. "
        "Tasks of the column the request leaves out keep their order after "
        "the ones it lists."
    ),
)
class TaskReorderView(TaskLotMixin, ProjectContextMixin, ReorderView):
    queryset = Task.objects.none()
    serializer_class = TaskReorderSerializer

    def perform_reorder(self, tasks, data):
        self._require_action("move")
        reorder_tasks(
            self.project,
            data["status"],
            [task.uuid for task in tasks],
            actor=self.request.user,
        )
        self.refuse_if_gone(tasks)


@extend_schema(tags=["Projects - Tasks"])
@extend_schema_view(
    put=extend_schema(summary="Watch or mute a task"),
    delete=extend_schema(summary="Drop your explicit watch state on a task"),
)
class TaskWatchView(TaskContextMixin, LinkView):
    """The caller's own watch state on one task.

    Open to any user with project access; archived projects stay writable
    here - the state is a per-user preference, not project data.
    """

    queryset = Task.objects.none()
    serializer_class = TaskWatchSerializer

    def perform_link(self, task, data):
        set_watch_state(task, self.request.user, muted=data["state"] == "muted")

    def perform_unlink(self, task):
        clear_watch_state(task, self.request.user)
