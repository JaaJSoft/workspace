from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import OuterRef, Subquery
from django.http import Http404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from workspace.common.uuids import parse_uuid_or_none

from .models import Label, Project, ProjectMember, Task, TaskStatus
from .queries import get_project_role, user_project_ids
from .serializers import (
    LabelSerializer,
    MemberRoleSerializer,
    MemberSerializer,
    MemberWriteSerializer,
    ProjectSerializer,
    TaskReorderSerializer,
    TaskSerializer,
    TaskStatusSerializer,
)
from .services.members import (
    ProjectRuleError,
    add_member,
    change_member_role,
    remove_member,
)
from .services.projects import create_project
from .services.search import fts_tasks
from .services.tasks import apply_status_change, create_task, reorder_tasks

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
        return Project.objects.filter(
            uuid__in=user_project_ids(self.request.user)
        ).annotate(_my_role=Subquery(my_role))

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        project = create_project(
            request.user,
            name=serializer.validated_data["name"],
            description=serializer.validated_data.get("description", ""),
            group=serializer.validated_data.get("group"),
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
        return super().partial_update(request, *args, **kwargs)

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


@extend_schema(tags=["Projects"])
class StatusViewSet(ProjectContextMixin, viewsets.GenericViewSet):
    serializer_class = TaskStatusSerializer
    pagination_class = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return TaskStatus.objects.none()
        return self.project.statuses.order_by("position", "created_at")

    def list(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return Response(serializer.data)


@extend_schema(tags=["Projects"])
class TaskViewSet(ProjectContextMixin, viewsets.ModelViewSet):
    serializer_class = TaskSerializer
    lookup_field = "uuid"
    lookup_url_kwarg = "task_uuid"
    pagination_class = None
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Task.objects.none()
        qs = (
            self.project.tasks.select_related("status")
            .prefetch_related("assignees", "labels")
            .order_by("position", "created_at")
        )
        if self.action != "list":
            return qs
        status_param = self.request.query_params.get("status")
        if status_param:
            parsed = parse_uuid_or_none(status_param)
            if parsed is None:
                raise ValidationError({"status": "Malformed UUID."})
            qs = qs.filter(status_id=parsed)
        assignee_param = self.request.query_params.get("assignee")
        if assignee_param:
            try:
                user_id = int(assignee_param)
            except (ValueError, TypeError) as exc:
                raise ValidationError({"assignee": "Invalid user ID."}) from exc
            qs = qs.filter(assignees=user_id)
        label_param = self.request.query_params.get("label")
        if label_param:
            parsed = parse_uuid_or_none(label_param)
            if parsed is None:
                raise ValidationError({"label": "Malformed UUID."})
            qs = qs.filter(labels=parsed)
        query = self.request.query_params.get("q")
        if query:
            qs = fts_tasks(qs, query)
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
        old_status_id = serializer.instance.status_id
        task = serializer.save()
        if task.status_id != old_status_id:
            apply_status_change(task)

    def destroy(self, request, *args, **kwargs):
        self._require_writable()
        return super().destroy(request, *args, **kwargs)

    def reorder(self, request, *args, **kwargs):
        """Single drag-and-drop endpoint: backlog sort, in-column sort and
        cross-column moves. Idempotent (safe with optimistic UI retries)."""
        self._require_writable()
        serializer = TaskReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        status_obj = self.project.statuses.filter(
            uuid=serializer.validated_data["status"]
        ).first()
        if status_obj is None:
            return Response(
                {"detail": "Unknown status for this project."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        reorder_tasks(self.project, status_obj, serializer.validated_data["order"])
        return Response({"success": True})
