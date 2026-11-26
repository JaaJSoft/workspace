from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.http import Http404
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import SAFE_METHODS
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.http_ranges import serve_with_ranges
from workspace.common.uuids import parse_uuid_or_none
from workspace.files.queries import resolve_accessible_files

from ..actions import ProjectActionRegistry
from ..models import (
    Epic,
    Label,
    Project,
    ProjectMember,
    ProjectNotificationLevel,
    Sprint,
    Subtask,
    Task,
    TaskAttachment,
    TaskComment,
    TaskFileLink,
    TaskLink,
    TaskStatus,
)
from ..queries import get_project_role, project_users, user_project_ids
from ..serializers import (
    EpicSerializer,
    LabelSerializer,
    MemberRoleSerializer,
    MemberSerializer,
    MemberWriteSerializer,
    ProjectConvertSerializer,
    ProjectNotificationLevelSerializer,
    ProjectSerializer,
    ProjectUserSerializer,
    ReorderSerializer,
    SprintCompleteSerializer,
    SprintSerializer,
    SubtaskSerializer,
    TaskAttachmentCreateSerializer,
    TaskAttachmentSerializer,
    TaskCommentBodySerializer,
    TaskCommentSerializer,
    TaskFileLinkCreateSerializer,
    TaskLinkCreateSerializer,
    TaskStatusSerializer,
)
from ..services.attachments import (
    MAX_ATTACHMENTS_PER_REQUEST,
    MAX_UPLOAD_BYTES,
    create_attachments,
    remove_attachment,
)
from ..services.comments import add_comment, notify_comment_edited
from ..services.conversion import convert_project_type
from ..services.epics import epics_with_progress
from ..services.file_links import file_links_for_task, link_files, unlink_file
from ..services.links import create_link, delete_link, links_for_task
from ..services.members import (
    ProjectRuleError,
    add_member,
    change_member_role,
    remove_member,
)
from ..services.projects import create_project
from ..services.sprints import (
    complete_sprint,
    delete_sprint,
    propagate_sprint_rename,
    start_sprint,
)
from ..services.statuses import create_status, delete_status, reorder_statuses
from ..services.subtasks import create_subtask, reorder_subtasks
from ..sse_provider import broadcast_project_change

User = get_user_model()


def _rule_error_response(exc):
    """Map a ProjectRuleError to a 400 with its curated detail message."""
    return Response({"detail": exc.detail}, status=status.HTTP_400_BAD_REQUEST)


def _unique_or_400(field, message, write, /, *args, **kwargs):
    """Run ``write`` in a savepoint, answering 400 on ``field`` if a unique constraint trips.

    The serializers check uniqueness first: this catches the writer that took
    the value in between.
    """
    try:
        with transaction.atomic():
            return write(*args, **kwargs)
    except IntegrityError:
        return Response({field: [message]}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(tags=["Projects"])
class ProjectViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectSerializer
    lookup_field = "uuid"
    pagination_class = None
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Project.objects.none()
        my_role = ProjectMember.objects.filter(
            project=OuterRef("pk"),
            user=self.request.user,
            left_at__isnull=True,
        ).values("role")[:1]
        return (
            Project.objects.filter(uuid__in=user_project_ids(self.request.user))
            # The serializer exposes groups as a PK list; without this every
            # project in the listing resolves its own auth_group query.
            .prefetch_related("groups")
            .annotate(_my_role=Subquery(my_role))
        )

    def create(self, request, *args, **kwargs):
        # Keys are auto-generated at creation; a client-supplied value is
        # dropped before validation so it cannot 400 a create.
        data = request.data.copy()
        data.pop("key", None)
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        project = create_project(
            request.user,
            name=serializer.validated_data["name"],
            description=serializer.validated_data.get("description", ""),
            groups=serializer.validated_data.get("groups"),
            project_type=serializer.validated_data.get("type", Project.Type.KANBAN),
        )
        project._my_role = ProjectMember.Role.ADMIN
        return Response(
            self.get_serializer(project).data, status=status.HTTP_201_CREATED
        )

    def partial_update(self, request, *args, **kwargs):
        project = self.get_object()
        self._require_action(project, "rename")
        if "groups" in request.data:
            self._require_action(project, "attach_group")
        return _unique_or_400(
            "key",
            _("Another project already uses this key."),
            super().partial_update,
            request,
            *args,
            **kwargs,
        )

    def destroy(self, request, *args, **kwargs):
        project = self.get_object()
        if project.type == Project.Type.PERSONAL:
            return Response(
                {"detail": _("Personal projects cannot be deleted.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        self._require_action(project, "delete")
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def convert(self, request, uuid=None):
        project = self.get_object()
        self._require_admin(project)
        if project.is_archived:
            raise PermissionDenied(_("Project is archived."))
        serializer = ProjectConvertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            convert_project_type(
                project, serializer.validated_data["type"], actor=request.user
            )
        except ProjectRuleError as exc:
            return _rule_error_response(exc)
        return Response(self.get_serializer(project).data)

    @action(detail=True, methods=["post"])
    def archive(self, request, uuid=None):
        project = self.get_object()
        if project.type == Project.Type.PERSONAL:
            return Response(
                {"detail": _("Personal projects cannot be archived.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        self._require_action(project, "archive")
        project.archived_at = timezone.now()
        project.save(update_fields=["archived_at", "updated_at"])
        return Response(self.get_serializer(project).data)

    @action(detail=True, methods=["post"])
    def unarchive(self, request, uuid=None):
        project = self.get_object()
        self._require_action(project, "unarchive")
        project.archived_at = None
        project.save(update_fields=["archived_at", "updated_at"])
        return Response(self.get_serializer(project).data)

    def get_object(self):
        self._object = super().get_object()
        return self._object

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        # A deleted project has no page left to refresh, a created one no
        # page open yet.
        if self.action in _PROJECT_CHANGES and _succeeded(response):
            project = getattr(self, "_object", None)
            if project is not None:
                broadcast_project_change(project)
        return response

    def _require_admin(self, project):
        if get_project_role(self.request.user, project) != ProjectMember.Role.ADMIN:
            raise PermissionDenied(_("Admin role required."))

    def _require_action(self, project, action_id):
        role = get_project_role(self.request.user, project)
        require_action(
            self.request.user, action_id, project, project=project, role=role
        )


_PROJECT_CHANGES = {"partial_update", "convert", "archive", "unarchive"}


def _succeeded(response):
    return response.status_code < 400


class ProjectContextMixin:
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
            and _succeeded(response)
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


@extend_schema(tags=["Projects"])
class MemberViewSet(ProjectContextMixin, viewsets.GenericViewSet):
    serializer_class = MemberSerializer
    lookup_field = "uuid"
    pagination_class = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ProjectMember.objects.none()
        return (
            self.project.members.filter(left_at__isnull=True)
            .select_related("user")
            .order_by("joined_at")
        )

    def list(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Users a task can be assigned to",
        responses=ProjectUserSerializer(many=True),
    )
    def users(self, request, *args, **kwargs):
        """The members and the users of the attached groups."""
        users = project_users(self.project)
        return Response(ProjectUserSerializer(users, many=True).data)

    def create(self, request, *args, **kwargs):
        self._require_action("manage_members", self.project)
        serializer = MemberWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(pk=serializer.validated_data["user"]).first()
        if user is None:
            return Response(
                {"detail": _("User not found.")}, status=status.HTTP_400_BAD_REQUEST
            )
        try:
            member = add_member(
                self.project, user, role=serializer.validated_data["role"]
            )
        except ProjectRuleError as exc:
            return _rule_error_response(exc)
        return Response(
            self.get_serializer(member).data, status=status.HTTP_201_CREATED
        )

    def partial_update(self, request, *args, **kwargs):
        self._require_action("manage_members", self.project)
        member = self.get_object()
        serializer = MemberRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            member = change_member_role(member, serializer.validated_data["role"])
        except ProjectRuleError as exc:
            return _rule_error_response(exc)
        return Response(self.get_serializer(member).data)

    def destroy(self, request, *args, **kwargs):
        member = self.get_object()
        if member.user_id != request.user.pk:
            self._require_action("manage_members", self.project)
        try:
            remove_member(member)
        except ProjectRuleError as exc:
            return _rule_error_response(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Projects"])
class ProjectNotificationLevelView(ProjectContextMixin, APIView):
    """The caller's own notification level override for one project.

    Open to any user with project access, group-granted users included -
    which is why this is not a member sub-resource. Archived projects stay
    writable here: the override is per-user preference, not project data.
    """

    broadcasts_changes = False

    @extend_schema(
        summary="Override your notification level for this project",
        request=ProjectNotificationLevelSerializer,
    )
    def put(self, request, project_uuid):
        serializer = ProjectNotificationLevelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        level = serializer.validated_data["level"]
        ProjectNotificationLevel.objects.update_or_create(
            project=self.project, user=request.user, defaults={"level": level}
        )
        return Response({"level": level})

    @extend_schema(
        summary="Drop your override and fall back to the module-wide setting"
    )
    def delete(self, request, project_uuid):
        ProjectNotificationLevel.objects.filter(
            project=self.project, user=request.user
        ).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Projects - Statuses & Labels"])
class LabelViewSet(ProjectContextMixin, viewsets.ModelViewSet):
    serializer_class = LabelSerializer
    lookup_field = "uuid"
    pagination_class = None
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Label.objects.none()
        return self.project.labels.order_by("name")

    def perform_create(self, serializer):
        serializer.save(project=self.project)

    def create(self, request, *args, **kwargs):
        self._require_action("manage_labels", self.project)
        return _unique_or_400(
            "name",
            _("A label with this name already exists in this project."),
            super().create,
            request,
            *args,
            **kwargs,
        )

    def partial_update(self, request, *args, **kwargs):
        self._require_action("manage_labels", self.project)
        return _unique_or_400(
            "name",
            _("A label with this name already exists in this project."),
            super().partial_update,
            request,
            *args,
            **kwargs,
        )

    def destroy(self, request, *args, **kwargs):
        self._require_action("manage_labels", self.project)
        return super().destroy(request, *args, **kwargs)


@extend_schema(tags=["Projects - Epics"])
class EpicViewSet(ProjectContextMixin, viewsets.ModelViewSet):
    serializer_class = EpicSerializer
    lookup_field = "uuid"
    pagination_class = None
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Epic.objects.none()
        return epics_with_progress(self.project)

    def perform_create(self, serializer):
        serializer.save(project=self.project)

    def create(self, request, *args, **kwargs):
        self._require_action("manage_epics", self.project)
        return _unique_or_400(
            "name",
            _("An epic with this name already exists in this project."),
            super().create,
            request,
            *args,
            **kwargs,
        )

    def partial_update(self, request, *args, **kwargs):
        self._require_action("manage_epics", self.project)
        return _unique_or_400(
            "name",
            _("An epic with this name already exists in this project."),
            super().partial_update,
            request,
            *args,
            **kwargs,
        )

    def destroy(self, request, *args, **kwargs):
        self._require_action("manage_epics", self.project)
        return super().destroy(request, *args, **kwargs)


@extend_schema(tags=["Projects - Sprints"])
class SprintViewSet(ProjectContextMixin, viewsets.ModelViewSet):
    serializer_class = SprintSerializer
    lookup_field = "uuid"
    pagination_class = None
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Sprint.objects.none()
        # One reverse-FK join serves both rollup counts (epics precedent).
        return self.project.sprints.annotate(
            task_count=Count("tasks"),
            done_task_count=Count(
                "tasks", filter=Q(tasks__status__category=TaskStatus.Category.DONE)
            ),
        ).order_by("created_at")

    def perform_create(self, serializer):
        serializer.save(project=self.project)

    def perform_update(self, serializer):
        old_name = serializer.instance.name
        sprint = serializer.save()
        if sprint.name != old_name:
            propagate_sprint_rename(sprint)

    def _require_scrum(self):
        """Sprints are the scrum board model; a kanban project only ever
        holds the closed ones a conversion left behind, as frozen history."""
        if self.project.type != Project.Type.SCRUM:
            raise PermissionDenied(_("Sprints require a scrum project."))

    def create(self, request, *args, **kwargs):
        self._require_action("manage_sprints", self.project)
        self._require_scrum()
        return _unique_or_400(
            "name",
            _("A sprint with this name already exists in this project."),
            super().create,
            request,
            *args,
            **kwargs,
        )

    def partial_update(self, request, *args, **kwargs):
        self._require_action("manage_sprints", self.project)
        self._require_scrum()
        return _unique_or_400(
            "name",
            _("A sprint with this name already exists in this project."),
            super().partial_update,
            request,
            *args,
            **kwargs,
        )

    def destroy(self, request, *args, **kwargs):
        self._require_action("manage_sprints", self.project)
        self._require_scrum()
        sprint = self.get_object()
        try:
            delete_sprint(sprint)
        except ProjectRuleError as exc:
            return _rule_error_response(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def start(self, request, *args, **kwargs):
        self._require_action("manage_sprints", self.project)
        self._require_scrum()
        sprint = self.get_object()
        try:
            start_sprint(sprint, actor=request.user)
        except ProjectRuleError as exc:
            return _rule_error_response(exc)
        return Response(self.get_serializer(self.get_object()).data)

    def complete(self, request, *args, **kwargs):
        self._require_action("manage_sprints", self.project)
        self._require_scrum()
        sprint = self.get_object()
        serializer = SprintCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        move_to = None
        move_to_uuid = serializer.validated_data["move_to"]
        if move_to_uuid is not None:
            move_to = self.project.sprints.filter(uuid=move_to_uuid).first()
            if move_to is None:
                return Response(
                    {"detail": _("Unknown target sprint for this project.")},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        try:
            complete_sprint(sprint, move_to=move_to, actor=request.user)
        except ProjectRuleError as exc:
            return _rule_error_response(exc)
        return Response(self.get_serializer(self.get_object()).data)


@extend_schema(tags=["Projects - Statuses & Labels"])
class StatusViewSet(ProjectContextMixin, viewsets.ModelViewSet):
    serializer_class = TaskStatusSerializer
    lookup_field = "uuid"
    pagination_class = None
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return TaskStatus.objects.none()
        return self.project.statuses.order_by("position", "created_at")

    def create(self, request, *args, **kwargs):
        self._require_admin()
        self._require_writable()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return _unique_or_400(
            "name",
            _("A column with this name already exists in this project."),
            self._create_status,
            serializer.validated_data,
        )

    def _create_status(self, fields):
        status_obj = create_status(self.project, **fields)
        return Response(
            self.get_serializer(status_obj).data, status=status.HTTP_201_CREATED
        )

    def partial_update(self, request, *args, **kwargs):
        self._require_admin()
        self._require_writable()
        return _unique_or_400(
            "name",
            _("A column with this name already exists in this project."),
            super().partial_update,
            request,
            *args,
            **kwargs,
        )

    def destroy(self, request, *args, **kwargs):
        self._require_admin()
        self._require_writable()
        status_obj = self.get_object()
        move_to = None
        move_to_param = request.query_params.get("move_to")
        if move_to_param:
            parsed = parse_uuid_or_none(move_to_param)
            if parsed is None:
                return Response(
                    {"detail": _("Malformed move_to UUID.")},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            move_to = self.project.statuses.filter(uuid=parsed).first()
            if move_to is None:
                return Response(
                    {"detail": _("Unknown target column for this project.")},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        try:
            delete_status(status_obj, move_to=move_to, actor=request.user)
        except ProjectRuleError as exc:
            return _rule_error_response(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def reorder(self, request, *args, **kwargs):
        self._require_admin()
        self._require_writable()
        serializer = ReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reorder_statuses(self.project, serializer.validated_data["order"])
        return Response({"success": True})


@extend_schema(tags=["Projects - Tasks"])
class SubtaskViewSet(ProjectContextMixin, viewsets.GenericViewSet):
    """Checklist items nested under a task; any member of a writable
    project can edit them, like comments."""

    serializer_class = SubtaskSerializer
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
            return Subtask.objects.none()
        return self.task.subtasks.order_by("position", "created_at")

    def list(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        self._require_action("edit", self.task)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        subtask = create_subtask(self.task, serializer.validated_data["title"])
        return Response(
            self.get_serializer(subtask).data, status=status.HTTP_201_CREATED
        )

    def partial_update(self, request, *args, **kwargs):
        self._require_action("edit", self.task)
        subtask = self.get_object()
        serializer = self.get_serializer(subtask, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        self._require_action("edit", self.task)
        self.get_object().delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def reorder(self, request, *args, **kwargs):
        self._require_action("edit", self.task)
        serializer = ReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reorder_subtasks(self.task, serializer.validated_data["order"])
        return Response({"success": True})


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
