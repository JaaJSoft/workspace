"""What every view of the module starts from: the project a request is about,
the caller's role in it, and the gates on what they may do there."""

from contextlib import contextmanager
from operator import attrgetter

from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils.translation import gettext as _
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import SAFE_METHODS

from workspace.common.exceptions import Conflict

from ..actions import ProjectActionRegistry
from ..models import Project, ProjectMember, Task
from ..queries import get_project_role
from ..services.members import ProjectRuleError
from ..sse_provider import broadcast_project_change


def succeeded(response):
    return response.status_code < 400


@contextmanager
def taken_value_refused(field, message):
    """Refuse the write under *field* when a unique constraint trips.

    The serializers check uniqueness first: this catches the writer that took
    the value in between. The savepoint keeps the request's transaction
    usable after the constraint fired.
    """
    try:
        with transaction.atomic():
            yield
    except IntegrityError:
        raise ValidationError({field: [message]}) from None


class RuleErrorsMixin:
    """A rule of the module a request runs into answers 409: the request is
    valid, the state of the project refuses it."""

    def handle_exception(self, exc):
        if isinstance(exc, ProjectRuleError):
            exc = Conflict(exc.detail)
        return super().handle_exception(exc)


class ProjectContextMixin(RuleErrorsMixin):
    """Resolve the project from the URL kwarg and the caller's role, once.

    404 both when the project does not exist and when the user has no
    access, so existence is never leaked. Mutating endpoints must call
    _require_admin/_require_writable explicitly on top.

    Every write that succeeds is announced to the project's open pages
    (broadcast_project_change); an endpoint whose writes change nothing they
    show turns ``broadcasts_changes`` off.
    """

    broadcasts_changes = True

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        project = self.resolve_project(**kwargs)
        role = get_project_role(request.user, project)
        if role is None:
            raise Http404
        self.project = project
        self.role = role

    def resolve_project(self, project_uuid, **kwargs):
        try:
            return Project.objects.get(uuid=project_uuid)
        except Project.DoesNotExist:
            raise Http404 from None

    def changed_task_uuid(self):
        """The task a write of this view changed, for the pages showing it."""
        return self.kwargs.get("task_uuid")

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        project = getattr(self, "project", None)
        if (
            self.broadcasts_changes
            and project is not None
            and request.method not in SAFE_METHODS
            and succeeded(response)
        ):
            broadcast_project_change(project, task_uuid=self.changed_task_uuid())
        return response

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["project"] = self.project
        return context

    def _require_admin(self):
        if self.role != ProjectMember.Role.ADMIN:
            raise PermissionDenied(_("Admin role required."))

    def _require_writable(self):
        if self.project.is_archived:
            raise PermissionDenied(_("Project is archived."))

    def _require_action(self, action_id, target=None):
        """Gate on *action_id* for *target*, by default a task of the project:
        no task action reads the row, only the project's state."""
        if target is None:
            target = Task(project=self.project)
        require_action(
            self.request.user, action_id, target, project=self.project, role=self.role
        )

    def _require_task_edit(self, data):
        """Every action a task field write performs, ``edit`` for the rest."""
        fields = set(data.keys()) if hasattr(data, "keys") else set()
        for action_id in sorted({_TASK_FIELD_ACTIONS.get(f, "edit") for f in fields}):
            self._require_action(action_id)
        if not fields:
            self._require_action("edit")

    def _require_bulk_edit(self, data):
        """The action of each operation a bulk edit asks for."""
        fields = set(data.keys()) if hasattr(data, "keys") else set()
        actions = {_BULK_EDIT_ACTIONS[f] for f in fields if f in _BULK_EDIT_ACTIONS}
        for action_id in sorted(actions) or ["edit"]:
            self._require_action(action_id)


class ProjectRowMixin(ProjectContextMixin):
    """The project and the caller's role, read from the row the path names.

    A row with a uuid of its own is addressed without its project
    (``/projects/labels/{uuid}``): the row says which project it is in. One
    of a project the caller cannot reach answers like one that does not exist.
    """

    # From the row to its project, as ``select_related`` spells it.
    project_path = "project"

    def rows(self):
        """Every row the path may name, whatever its project."""
        return self.queryset.model.objects.all()

    def resolve_project(self, uuid, **kwargs):
        row = self.rows().select_related(self.project_path).filter(uuid=uuid).first()
        if row is None:
            raise Http404
        self.row = row
        return attrgetter(self.project_path.replace("__", "."))(row)

    def get_object(self):
        return self.row


class TaskRowsMixin(ProjectContextMixin):
    """The project and the caller's role, read from the task a collection
    hangs under (``/projects/tasks/{task_uuid}/subtasks``)."""

    def resolve_project(self, task_uuid, **kwargs):
        try:
            self.task = Task.objects.select_related("project").get(uuid=task_uuid)
        except Task.DoesNotExist:
            raise Http404 from None
        return self.task.project


class ManagedRowsMixin:
    """Rows any member reads and the project's admins write: every request
    that is not a read asks ``manage_action`` of the project first."""

    manage_action = None

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.method not in SAFE_METHODS:
            self.require_manage()

    def require_manage(self):
        self._require_action(self.manage_action, self.project)


class NamedRowWritesMixin:
    """Create and update of a row whose name is unique in its project."""

    # What a writer who lost the name to another one is told.
    name_taken = None

    def perform_create(self, serializer):
        with taken_value_refused("name", self.name_taken):
            serializer.save(project=self.project)

    def perform_update(self, serializer):
        with taken_value_refused("name", self.name_taken):
            serializer.save()


# The registry action a write to each task field performs.
_TASK_FIELD_ACTIONS = {
    "assignees": "assign",
    "labels": "set_labels",
    "epic": "set_epic",
    "due_date": "set_due",
    "priority": "set_priority",
    "status": "move",
}
_BULK_EDIT_ACTIONS = {
    "assign": "assign",
    "unassign": "assign",
    "add_labels": "set_labels",
    "remove_labels": "set_labels",
    "priority": "set_priority",
    "due_date": "set_due",
}


def require_action(user, action_id, target, *, project, role):
    """Raise 403 unless the action registry offers *action_id* on *target*.

    The registry is the write gate, not only the menu: an endpoint asks it
    what the context menu asks, so the two cannot disagree.
    """
    action = ProjectActionRegistry.action_for(action_id, target)
    if ProjectActionRegistry.is_action_available(
        action_id, user, target, role=role, archived=project.is_archived
    ):
        return
    if action is not None and action.min_role == ProjectMember.Role.ADMIN:
        if role != ProjectMember.Role.ADMIN:
            raise PermissionDenied(_("Admin role required."))
    if project.is_archived and not (action and action.available_when_archived):
        raise PermissionDenied(_("Project is archived."))
    raise PermissionDenied(_("This action is not available on this project."))
