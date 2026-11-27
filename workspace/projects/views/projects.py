from django.db.models import OuterRef, Subquery
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import (
    GenericAPIView,
    ListCreateAPIView,
    RetrieveUpdateDestroyAPIView,
)
from rest_framework.response import Response

from workspace.common.views import LinkView

from ..filters import ProjectFilter
from ..models import Project, ProjectMember, ProjectNotificationLevel
from ..queries import get_project_role, user_project_ids
from ..serializers import (
    ProjectConvertSerializer,
    ProjectNotificationLevelSerializer,
    ProjectSerializer,
)
from ..services.conversion import convert_project_type
from ..services.projects import create_project
from ..sse_provider import broadcast_project_change
from .context import (
    ProjectContextMixin,
    RuleErrorsMixin,
    require_action,
    taken_value_refused,
)


def _visible_projects(user):
    my_role = ProjectMember.objects.filter(
        project=OuterRef("pk"), user=user, left_at__isnull=True
    ).values("role")[:1]
    return (
        Project.objects.filter(uuid__in=user_project_ids(user))
        # One query for the groups of the whole page, not one per project.
        .prefetch_related("groups")
        .annotate(_my_role=Subquery(my_role))
    )


@extend_schema(tags=["Projects"])
@extend_schema_view(
    get=extend_schema(summary="List the projects the caller can open"),
    post=extend_schema(summary="Create a project"),
)
class ProjectListView(ListCreateAPIView):
    queryset = Project.objects.none()
    serializer_class = ProjectSerializer
    filterset_class = ProjectFilter

    def get_queryset(self):
        return _visible_projects(self.request.user).order_by("name", "uuid")

    def create(self, request, *args, **kwargs):
        # Keys are auto-generated at creation; a client-supplied value is
        # dropped before validation so it cannot 400 a create.
        data = request.data.copy()
        data.pop("key", None)
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        asked = serializer.validated_data
        project = create_project(
            request.user,
            name=asked["name"],
            description=asked.get("description", ""),
            groups=asked.get("groups"),
            project_type=asked.get("type", Project.Type.KANBAN),
            done_retention_days=asked.get("done_retention_days"),
            estimate_unit=asked.get("estimate_unit", ""),
            archived=asked.get("is_archived", False),
        )
        project._my_role = ProjectMember.Role.ADMIN
        return Response(
            self.get_serializer(project).data, status=status.HTTP_201_CREATED
        )


class _OneProjectMixin(RuleErrorsMixin):
    """The project the path names, among the ones the caller can open."""

    queryset = Project.objects.none()
    lookup_field = "uuid"

    def get_queryset(self):
        return _visible_projects(self.request.user)

    def get_object(self):
        if not hasattr(self, "_project"):
            self._project = super().get_object()
        return self._project

    def _role(self, project):
        return get_project_role(self.request.user, project)

    def _require_admin(self, project):
        if self._role(project) != ProjectMember.Role.ADMIN:
            raise PermissionDenied(_("Admin role required."))

    def _require_action(self, project, action_id):
        require_action(
            self.request.user,
            action_id,
            project,
            project=project,
            role=self._role(project),
        )


@extend_schema(tags=["Projects"])
@extend_schema_view(
    get=extend_schema(summary="Get a project"),
    patch=extend_schema(
        summary="Update a project",
        description=(
            "`is_archived` archives the project or brings it back; an "
            "archived project takes no other change. The board model is "
            "changed through `/convert`."
        ),
    ),
    delete=extend_schema(summary="Delete a project"),
)
class ProjectDetailView(_OneProjectMixin, RetrieveUpdateDestroyAPIView):
    serializer_class = ProjectSerializer
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def update(self, request, *args, **kwargs):
        project = self.get_object()
        fields = set(request.data.keys()) if hasattr(request.data, "keys") else set()
        # Archiving is its own action: it is the one write an archived
        # project still takes.
        if fields != {"is_archived"}:
            self._require_action(project, "rename")
        if "groups" in fields:
            self._require_action(project, "attach_group")
        serializer = self.get_serializer(
            project,
            data=request.data,
            partial=True,
            context={**self.get_serializer_context(), "project": project},
        )
        serializer.is_valid(raise_exception=True)
        if "is_archived" in serializer.validated_data:
            self._require_archiving(project, serializer.validated_data["is_archived"])
        with taken_value_refused("key", _("Another project already uses this key.")):
            serializer.save()
        broadcast_project_change(project)
        return Response(serializer.data)

    def _require_archiving(self, project, archived):
        if archived == project.is_archived:
            # Nothing to change, and still not a plain member's field.
            self._require_admin(project)
            return
        if archived and project.type == Project.Type.PERSONAL:
            raise PermissionDenied(_("Personal projects cannot be archived."))
        self._require_action(project, "archive" if archived else "unarchive")

    def destroy(self, request, *args, **kwargs):
        project = self.get_object()
        if project.type == Project.Type.PERSONAL:
            raise PermissionDenied(_("Personal projects cannot be deleted."))
        self._require_action(project, "delete")
        return super().destroy(request, *args, **kwargs)


@extend_schema(
    tags=["Projects"],
    summary="Switch a project between kanban and scrum",
    request=ProjectConvertSerializer,
    responses=ProjectSerializer,
)
class ProjectConvertView(_OneProjectMixin, GenericAPIView):
    serializer_class = ProjectSerializer

    def post(self, request, *args, **kwargs):
        project = self.get_object()
        self._require_admin(project)
        if project.is_archived:
            raise PermissionDenied(_("Project is archived."))
        if project.type == Project.Type.PERSONAL:
            raise PermissionDenied(_("Personal projects cannot change type."))
        body = ProjectConvertSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        convert_project_type(project, body.validated_data["type"], actor=request.user)
        broadcast_project_change(project)
        return Response(self.get_serializer(project).data)


@extend_schema(tags=["Projects"])
@extend_schema_view(
    put=extend_schema(summary="Override your notification level for a project"),
    delete=extend_schema(
        summary="Drop your override and fall back to the module-wide setting"
    ),
)
class ProjectNotificationLevelView(ProjectContextMixin, LinkView):
    """The caller's own notification level override for one project.

    Open to any user with project access, group-granted users included -
    which is why this is not a member sub-resource. Archived projects stay
    writable here: the override is per-user preference, not project data.
    """

    queryset = Project.objects.none()
    serializer_class = ProjectNotificationLevelSerializer
    broadcasts_changes = False

    def resolve_project(self, uuid, **kwargs):
        return super().resolve_project(uuid)

    def get_object(self):
        return self.project

    def perform_link(self, project, data):
        ProjectNotificationLevel.objects.update_or_create(
            project=project, user=self.request.user, defaults={"level": data["level"]}
        )

    def perform_unlink(self, project):
        ProjectNotificationLevel.objects.filter(
            project=project, user=self.request.user
        ).delete()
