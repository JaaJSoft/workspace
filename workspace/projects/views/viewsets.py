from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.http import Http404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.http_ranges import serve_with_ranges
from workspace.common.pagination import OptInLimitOffsetPagination
from workspace.common.uuids import parse_uuid_or_none
from workspace.files.services import FileService

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
    TaskEvent,
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
    ReorderSerializer,
    SprintCompleteSerializer,
    SprintSerializer,
    SubtaskSerializer,
    TaskAttachmentCreateSerializer,
    TaskAttachmentSerializer,
    TaskCommentBodySerializer,
    TaskCommentSerializer,
    TaskLinkCreateSerializer,
    TaskMoveSerializer,
    TaskReorderSerializer,
    TaskSerializer,
    TaskSprintSerializer,
    TaskStatusSerializer,
    TaskWatchSerializer,
)
from ..services.assignments import notify_assigned
from ..services.attachments import (
    MAX_ATTACHMENTS_PER_REQUEST,
    MAX_UPLOAD_BYTES,
    create_attachments,
    remove_attachment,
)
from ..services.comments import add_comment, notify_comment_edited
from ..services.conversion import convert_project_type
from ..services.estimates import format_estimate
from ..services.events import record_task_event
from ..services.links import create_link, delete_link, links_for_task
from ..services.members import (
    ProjectRuleError,
    add_member,
    change_member_role,
    remove_member,
)
from ..services.projects import create_project
from ..services.sprints import (
    assign_tasks_to_sprint,
    complete_sprint,
    propagate_sprint_rename,
    start_sprint,
)
from ..services.statuses import create_status, delete_status, reorder_statuses
from ..services.subtasks import create_subtask, reorder_subtasks
from ..services.task_filters import (
    ORDERABLE_FIELDS,
    TaskFilterError,
    apply_task_filters,
    apply_task_ordering,
)
from ..services.tasks import (
    apply_status_change,
    create_task,
    delete_task,
    has_field_updates,
    move_tasks,
    reorder_tasks,
    settle_task_notifications,
)
from ..services.watchers import auto_watch, clear_watch_state, set_watch_state

User = get_user_model()


def _rule_error_response(exc):
    """Map a ProjectRuleError to a 400 with its curated detail message."""
    return Response({"detail": exc.detail}, status=status.HTTP_400_BAD_REQUEST)


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
        self._require_admin(project)
        if project.is_archived:
            raise PermissionDenied("Project is archived.")
        try:
            with transaction.atomic():
                return super().partial_update(request, *args, **kwargs)
        except IntegrityError:
            return Response(
                {"key": ["Another project already uses this key."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def destroy(self, request, *args, **kwargs):
        project = self.get_object()
        self._require_admin(project)
        if project.type == Project.Type.PERSONAL:
            return Response(
                {"detail": "Personal projects cannot be deleted."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def convert(self, request, uuid=None):
        project = self.get_object()
        self._require_admin(project)
        if project.is_archived:
            raise PermissionDenied("Project is archived.")
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
        self._require_admin(project)
        if project.type == Project.Type.PERSONAL:
            return Response(
                {"detail": "Personal projects cannot be archived."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if project.archived_at is None:
            project.archived_at = timezone.now()
            project.save(update_fields=["archived_at", "updated_at"])
        return Response(self.get_serializer(project).data)

    @action(detail=True, methods=["post"])
    def unarchive(self, request, uuid=None):
        project = self.get_object()
        self._require_admin(project)
        if project.archived_at is not None:
            project.archived_at = None
            project.save(update_fields=["archived_at", "updated_at"])
        return Response(self.get_serializer(project).data)

    def _require_admin(self, project):
        if get_project_role(self.request.user, project) != ProjectMember.Role.ADMIN:
            raise PermissionDenied("Admin role required.")


class ProjectContextMixin:
    """Resolve the project from the URL kwarg and the caller's role, once.

    404 both when the project does not exist and when the user has no
    access, so existence is never leaked. Mutating endpoints must call
    _require_admin/_require_writable explicitly on top.
    """

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            project = Project.objects.get(uuid=kwargs["project_uuid"])
        except Project.DoesNotExist:
            raise Http404 from None
        role = get_project_role(request.user, project)
        if role is None:
            raise Http404
        self.project = project
        self.role = role

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["project"] = self.project
        return context

    def _require_admin(self):
        if self.role != ProjectMember.Role.ADMIN:
            raise PermissionDenied("Admin role required.")

    def _require_writable(self):
        if self.project.is_archived:
            raise PermissionDenied("Project is archived.")


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

    def create(self, request, *args, **kwargs):
        self._require_admin()
        self._require_writable()
        serializer = MemberWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(pk=serializer.validated_data["user"]).first()
        if user is None:
            return Response(
                {"detail": "User not found."}, status=status.HTTP_400_BAD_REQUEST
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
        self._require_admin()
        self._require_writable()
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
            self._require_admin()
            self._require_writable()
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


@extend_schema(tags=["Projects - Tasks"])
class TaskWatchView(ProjectContextMixin, APIView):
    """The caller's own watch state on one task.

    Open to any user with project access; archived projects stay writable
    here - the state is a per-user preference, not project data.
    """

    def _task(self, task_uuid):
        try:
            return self.project.tasks.get(uuid=task_uuid)
        except Task.DoesNotExist:
            raise Http404 from None

    @extend_schema(summary="Watch or mute this task", request=TaskWatchSerializer)
    def put(self, request, project_uuid, task_uuid):
        serializer = TaskWatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        state = serializer.validated_data["state"]
        set_watch_state(self._task(task_uuid), request.user, muted=state == "muted")
        return Response({"state": state})

    @extend_schema(summary="Drop your explicit watch state")
    def delete(self, request, project_uuid, task_uuid):
        clear_watch_state(self._task(task_uuid), request.user)
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
        self._require_admin()
        self._require_writable()
        try:
            with transaction.atomic():
                return super().create(request, *args, **kwargs)
        except IntegrityError:
            return Response(
                {"name": ["A label with this name already exists in this project."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def partial_update(self, request, *args, **kwargs):
        self._require_admin()
        self._require_writable()
        try:
            with transaction.atomic():
                return super().partial_update(request, *args, **kwargs)
        except IntegrityError:
            return Response(
                {"name": ["A label with this name already exists in this project."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def destroy(self, request, *args, **kwargs):
        self._require_admin()
        self._require_writable()
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
        # One reverse-FK join serves both rollup counts; no distinct needed
        # since no other multi-valued relation is joined here.
        return self.project.epics.annotate(
            task_count=Count("tasks"),
            done_task_count=Count(
                "tasks", filter=Q(tasks__status__category=TaskStatus.Category.DONE)
            ),
        ).order_by("name")

    def perform_create(self, serializer):
        serializer.save(project=self.project)

    def create(self, request, *args, **kwargs):
        self._require_admin()
        self._require_writable()
        try:
            with transaction.atomic():
                return super().create(request, *args, **kwargs)
        except IntegrityError:
            return Response(
                {"name": ["An epic with this name already exists in this project."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def partial_update(self, request, *args, **kwargs):
        self._require_admin()
        self._require_writable()
        try:
            with transaction.atomic():
                return super().partial_update(request, *args, **kwargs)
        except IntegrityError:
            return Response(
                {"name": ["An epic with this name already exists in this project."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def destroy(self, request, *args, **kwargs):
        self._require_admin()
        self._require_writable()
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
            raise PermissionDenied("Sprints require a scrum project.")

    def create(self, request, *args, **kwargs):
        self._require_admin()
        self._require_writable()
        self._require_scrum()
        try:
            with transaction.atomic():
                return super().create(request, *args, **kwargs)
        except IntegrityError:
            return Response(
                {"name": ["A sprint with this name already exists in this project."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def partial_update(self, request, *args, **kwargs):
        self._require_admin()
        self._require_writable()
        self._require_scrum()
        try:
            with transaction.atomic():
                return super().partial_update(request, *args, **kwargs)
        except IntegrityError:
            return Response(
                {"name": ["A sprint with this name already exists in this project."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def destroy(self, request, *args, **kwargs):
        self._require_admin()
        self._require_writable()
        self._require_scrum()
        sprint = self.get_object()
        if sprint.state == Sprint.State.ACTIVE:
            return Response(
                {"detail": "The active sprint must be completed, not deleted."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)

    def start(self, request, *args, **kwargs):
        self._require_admin()
        self._require_writable()
        self._require_scrum()
        sprint = self.get_object()
        try:
            start_sprint(sprint, actor=request.user)
        except ProjectRuleError as exc:
            return _rule_error_response(exc)
        return Response(self.get_serializer(self.get_object()).data)

    def complete(self, request, *args, **kwargs):
        self._require_admin()
        self._require_writable()
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
                    {"detail": "Unknown target sprint for this project."},
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
        try:
            with transaction.atomic():
                status_obj = create_status(self.project, **serializer.validated_data)
        except IntegrityError:
            return Response(
                {"name": ["A column with this name already exists in this project."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            self.get_serializer(status_obj).data, status=status.HTTP_201_CREATED
        )

    def partial_update(self, request, *args, **kwargs):
        self._require_admin()
        self._require_writable()
        try:
            with transaction.atomic():
                return super().partial_update(request, *args, **kwargs)
        except IntegrityError:
            return Response(
                {"name": ["A column with this name already exists in this project."]},
                status=status.HTTP_400_BAD_REQUEST,
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
                    {"detail": "Malformed move_to UUID."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            move_to = self.project.statuses.filter(uuid=parsed).first()
            if move_to is None:
                return Response(
                    {"detail": "Unknown target column for this project."},
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
@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(
                "q",
                OpenApiTypes.STR,
                description="Full-text search on title and description.",
            ),
            OpenApiParameter(
                "status",
                OpenApiTypes.UUID,
                many=True,
                description="Only tasks in these columns.",
            ),
            OpenApiParameter(
                "assignee",
                OpenApiTypes.STR,
                many=True,
                description="Only tasks assigned to these user IDs; the literal `none` matches unassigned tasks.",
            ),
            OpenApiParameter(
                "label",
                OpenApiTypes.UUID,
                many=True,
                description="Only tasks carrying these labels.",
            ),
            OpenApiParameter(
                "epic",
                OpenApiTypes.UUID,
                many=True,
                description="Only tasks belonging to these epics.",
            ),
            OpenApiParameter(
                "priority",
                OpenApiTypes.STR,
                enum=Task.Priority.values,
                description="Only tasks with this priority.",
            ),
            OpenApiParameter(
                "due_before",
                OpenApiTypes.DATE,
                description="Only tasks due on or before this date.",
            ),
            OpenApiParameter(
                "due_after",
                OpenApiTypes.DATE,
                description="Only tasks due on or after this date.",
            ),
            OpenApiParameter(
                "created_by",
                OpenApiTypes.INT,
                description="Only tasks created by this user ID.",
            ),
            OpenApiParameter(
                "completed",
                OpenApiTypes.BOOL,
                description="True keeps only tasks in a done column, false only open tasks.",
            ),
            OpenApiParameter(
                "ordering",
                OpenApiTypes.STR,
                enum=sorted([*ORDERABLE_FIELDS, *(f"-{f}" for f in ORDERABLE_FIELDS)]),
                description="Sort field, descending with a `-` prefix. `priority` sorts most important first; tasks without a due date always sort last.",
            ),
        ]
    )
)
class TaskViewSet(ProjectContextMixin, viewsets.ModelViewSet):
    serializer_class = TaskSerializer
    lookup_field = "uuid"
    lookup_url_kwarg = "task_uuid"
    pagination_class = OptInLimitOffsetPagination
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Task.objects.none()
        qs = (
            self.project.tasks.select_related("status", "epic")
            .prefetch_related("assignees", "labels")
            .order_by("position", "created_at")
        )
        if self.action != "list":
            return qs
        try:
            qs = apply_task_filters(qs, self.request.query_params)
            qs = apply_task_ordering(qs, self.request.query_params)
        except TaskFilterError as exc:
            raise ValidationError({exc.field: exc.message}) from exc
        return qs.distinct()

    def create(self, request, *args, **kwargs):
        self._require_writable()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = create_task(self.project, request.user, **serializer.validated_data)
        return Response(self.get_serializer(task).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        self._require_writable()
        return super().partial_update(request, *args, **kwargs)

    def perform_update(self, serializer):
        old_status = serializer.instance.status
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
        task = serializer.save()
        if task.status_id != old_status.pk:
            apply_status_change(task, actor=self.request.user, old_status=old_status)
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
        if task.sprint_id != (old_sprint.pk if old_sprint else None):
            # Sprint names snapshotted, same rationale as the epic names.
            record_task_event(
                task,
                type=TaskEvent.Type.SPRINT,
                actor=self.request.user,
                from_value=old_sprint.name if old_sprint else "",
                to_value=task.sprint.name if task.sprint else "",
                from_ref=old_sprint.pk if old_sprint else None,
                to_ref=task.sprint_id,
            )
        if task.due_date != old_due_date and (
            task.due_date is None or task.due_date > timezone.localdate()
        ):
            settle_task_notifications([task])

    def destroy(self, request, *args, **kwargs):
        self._require_writable()
        return super().destroy(request, *args, **kwargs)

    def perform_destroy(self, instance):
        delete_task(instance, actor=self.request.user)

    def reorder(self, request, *args, **kwargs):
        """Single drag-and-drop endpoint: backlog sort, in-column sort and
        cross-column moves. Idempotent (safe with optimistic UI retries)."""
        self._require_writable()
        serializer = TaskReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        status_obj = self._resolve_status(serializer.validated_data["status"])
        if status_obj is None:
            return Response(
                {"detail": "Unknown status for this project."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        reorder_tasks(
            self.project,
            status_obj,
            serializer.validated_data["order"],
            actor=request.user,
        )
        return Response({"success": True})

    def move(self, request, *args, **kwargs):
        """Bulk move (backlog multi-select "send to board"): appends the
        listed tasks to the end of the target column. Idempotent."""
        self._require_writable()
        serializer = TaskMoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        status_obj = self._resolve_status(serializer.validated_data["status"])
        if status_obj is None:
            return Response(
                {"detail": "Unknown status for this project."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        moved = move_tasks(
            self.project,
            status_obj,
            serializer.validated_data["tasks"],
            actor=request.user,
        )
        return Response({"success": True, "moved": len(moved)})

    def assign_sprint(self, request, *args, **kwargs):
        """Bulk sprint assignment (backlog planning): sets or clears the
        sprint of the listed tasks without touching their status. Idempotent."""
        self._require_writable()
        serializer = TaskSprintSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sprint = None
        sprint_uuid = serializer.validated_data["sprint"]
        if sprint_uuid is not None:
            sprint = self.project.sprints.filter(uuid=sprint_uuid).first()
            if sprint is None:
                return Response(
                    {"detail": "Unknown sprint for this project."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        try:
            changed = assign_tasks_to_sprint(
                self.project,
                sprint,
                serializer.validated_data["tasks"],
                actor=request.user,
            )
        except ProjectRuleError as exc:
            return _rule_error_response(exc)
        return Response({"success": True, "updated": len(changed)})

    def _resolve_status(self, status_uuid):
        return self.project.statuses.filter(uuid=status_uuid).first()


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
        self._require_writable()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        subtask = create_subtask(self.task, serializer.validated_data["title"])
        return Response(
            self.get_serializer(subtask).data, status=status.HTTP_201_CREATED
        )

    def partial_update(self, request, *args, **kwargs):
        self._require_writable()
        subtask = self.get_object()
        serializer = self.get_serializer(subtask, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        self._require_writable()
        self.get_object().delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def reorder(self, request, *args, **kwargs):
        self._require_writable()
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
        self._require_writable()
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
                {"detail": "Task not found."}, status=status.HTTP_404_NOT_FOUND
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
        self._require_writable()
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
        self._require_writable()
        body_ser = TaskCommentBodySerializer(data=request.data)
        body_ser.is_valid(raise_exception=True)
        comment = add_comment(self.task, request.user, body_ser.validated_data["body"])
        return Response(
            self.get_serializer(comment).data, status=status.HTTP_201_CREATED
        )

    def partial_update(self, request, *args, **kwargs):
        self._require_writable()
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
        self._require_writable()
        comment = self._get_own_comment(request)
        comment.deleted_at = timezone.now()
        comment.save(update_fields=["deleted_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _get_own_comment(self, request):
        comment = self.get_queryset().filter(uuid=self.kwargs["uuid"]).first()
        if comment is None:
            raise Http404
        if comment.author_id != request.user.pk:
            raise PermissionDenied("You can only modify your own comments.")
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
        self._require_writable()
        ser = TaskAttachmentCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        uploads = request.FILES.getlist("files")
        file_uuids = ser.validated_data["file_uuids"]

        if not uploads and not file_uuids:
            return Response(
                {"detail": "Provide files or file_uuids."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(uploads) + len(file_uuids) > MAX_ATTACHMENTS_PER_REQUEST:
            return Response(
                {
                    "detail": "Maximum "
                    f"{MAX_ATTACHMENTS_PER_REQUEST} attachments per request."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        for f in uploads:
            if f.size > MAX_UPLOAD_BYTES:
                return Response(
                    {"detail": f'File "{f.name}" exceeds the 50 MB limit.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        picked = FileService.resolve_accessible_files(request.user, file_uuids)
        if picked is None:
            return Response(
                {"detail": "One or more files not found or not accessible."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                create_attachments(request.user, self.task, uploads, picked)
        except FileNotFoundError, OSError:
            return Response(
                {"detail": "One or more workspace file contents are unavailable."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {"attachments": self.get_serializer(self.get_queryset(), many=True).data},
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        self._require_writable()
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
