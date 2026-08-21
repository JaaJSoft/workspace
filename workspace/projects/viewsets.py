from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import OuterRef, Subquery
from django.http import Http404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from workspace.common.pagination import OptInLimitOffsetPagination
from workspace.common.uuids import parse_uuid_or_none
from workspace.files.services import FileService

from .models import (
    Label,
    Project,
    ProjectMember,
    Subtask,
    Task,
    TaskAttachment,
    TaskComment,
    TaskEvent,
    TaskStatus,
)
from .queries import get_project_role, project_users, user_project_ids
from .serializers import (
    LabelSerializer,
    MemberRoleSerializer,
    MemberSerializer,
    MemberWriteSerializer,
    ProjectSerializer,
    ReorderSerializer,
    SubtaskSerializer,
    TaskAttachmentCreateSerializer,
    TaskAttachmentSerializer,
    TaskCommentBodySerializer,
    TaskCommentSerializer,
    TaskMoveSerializer,
    TaskReorderSerializer,
    TaskSerializer,
    TaskStatusSerializer,
)
from .services.assignments import notify_assigned
from .services.attachments import (
    MAX_ATTACHMENTS_PER_REQUEST,
    MAX_UPLOAD_BYTES,
    attach_files,
    uploads_folder,
    visible_attachments,
)
from .services.comments import notify_comment_added, notify_comment_edited
from .services.estimates import format_estimate
from .services.events import record_task_event
from .services.members import (
    ProjectRuleError,
    add_member,
    change_member_role,
    remove_member,
)
from .services.projects import create_project
from .services.statuses import create_status, delete_status, reorder_statuses
from .services.subtasks import create_subtask, reorder_subtasks
from .services.task_filters import (
    ORDERABLE_FIELDS,
    TaskFilterError,
    apply_task_filters,
    apply_task_ordering,
)
from .services.tasks import (
    apply_status_change,
    create_task,
    delete_task,
    has_field_updates,
    move_tasks,
    reorder_tasks,
    settle_task_notifications,
)

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


@extend_schema(tags=["Projects"])
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
            self.project.tasks.select_related("status")
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

    def _resolve_status(self, status_uuid):
        return self.project.statuses.filter(uuid=status_uuid).first()


@extend_schema(tags=["Projects"])
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


@extend_schema(tags=["Projects"])
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
        comment = TaskComment.objects.create(
            task=self.task,
            author=request.user,
            body=body_ser.validated_data["body"],
        )
        record_task_event(self.task, type=TaskEvent.Type.COMMENTED, actor=request.user)
        notify_comment_added(self.task, request.user, comment.body)
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


@extend_schema(tags=["Projects"])
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
        return self.task.attachments.select_related("file", "added_by")

    def _visible(self):
        return visible_attachments(self.request.user, self.task)

    def list(self, request, *args, **kwargs):
        return Response(
            {"attachments": self.get_serializer(self._visible(), many=True).data}
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

        folder = uploads_folder(request.user) if uploads else None
        uploaded = [
            FileService.create_file(request.user, f.name, folder, content=f)
            for f in uploads
        ]
        attach_files(request.user, self.task, uploaded + picked)
        return Response(
            {"attachments": self.get_serializer(self._visible(), many=True).data},
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        self._require_writable()
        link = self.get_queryset().filter(uuid=kwargs["uuid"]).first()
        if link is None:
            raise Http404
        link.delete()
        record_task_event(self.task, type=TaskEvent.Type.DETACHED, actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
