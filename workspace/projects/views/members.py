from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.generics import (
    ListAPIView,
    ListCreateAPIView,
    RetrieveUpdateDestroyAPIView,
)
from rest_framework.response import Response

from ..models import ProjectMember
from ..queries import project_users
from ..serializers import (
    MemberSerializer,
    ProjectMemberRoleSerializer,
    ProjectUserSerializer,
)
from ..services.members import add_member, change_member_role, remove_member
from .context import ProjectContextMixin, ProjectRowMixin

User = get_user_model()


def _active_members():
    return ProjectMember.objects.filter(left_at__isnull=True).select_related("user")


@extend_schema(tags=["Projects"])
@extend_schema_view(
    get=extend_schema(summary="List the members of a project"),
    post=extend_schema(
        summary="Add a member to a project",
        description=(
            "Adding someone who left brings them back; adding an active "
            "member sets their role."
        ),
    ),
)
class MemberListView(ProjectContextMixin, ListCreateAPIView):
    queryset = ProjectMember.objects.none()
    serializer_class = MemberSerializer
    # A project's team, listed whole.
    pagination_class = None

    def get_queryset(self):
        return _active_members().filter(project=self.project).order_by("joined_at")

    def create(self, request, *args, **kwargs):
        self._require_action("manage_members", self.project)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = add_member(
            self.project,
            serializer.validated_data["user"],
            role=serializer.validated_data["role"],
        )
        return Response(
            self.get_serializer(member).data, status=status.HTTP_201_CREATED
        )


@extend_schema(tags=["Projects"])
@extend_schema_view(
    get=extend_schema(summary="Get a member of a project"),
    patch=extend_schema(
        summary="Change the role of a member", request=ProjectMemberRoleSerializer
    ),
    delete=extend_schema(
        summary="Remove a member from a project",
        description="Anyone may remove themselves; removing someone else takes an admin.",
    ),
)
class MemberDetailView(ProjectRowMixin, RetrieveUpdateDestroyAPIView):
    queryset = ProjectMember.objects.none()
    serializer_class = MemberSerializer
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def rows(self):
        return _active_members()

    def update(self, request, *args, **kwargs):
        self._require_action("manage_members", self.project)
        body = ProjectMemberRoleSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        member = change_member_role(self.row, body.validated_data["role"])
        return Response(self.get_serializer(member).data)

    def destroy(self, request, *args, **kwargs):
        if self.row.user_id != request.user.pk:
            self._require_action("manage_members", self.project)
        remove_member(self.row)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=["Projects"],
    summary="List the users a task of a project can be assigned to",
    description="The members, and the users of the groups the project is attached to.",
)
class ProjectUserListView(ProjectContextMixin, ListAPIView):
    queryset = User.objects.none()
    serializer_class = ProjectUserSerializer
    pagination_class = None

    def get_queryset(self):
        return project_users(self.project)
