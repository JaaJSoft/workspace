from django.utils.translation import gettext_lazy
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView

from workspace.common.views import ReorderView

from ..models import TaskStatus
from ..serializers import StatusDeleteQuerySerializer, TaskStatusSerializer
from ..services.statuses import create_status, delete_status, reorder_statuses
from .context import (
    ManagedRowsMixin,
    NamedRowWritesMixin,
    ProjectContextMixin,
    ProjectRowMixin,
    taken_value_refused,
)


class _BoardSetupMixin(ManagedRowsMixin):
    """The columns are the admins' to change, while the project is open."""

    queryset = TaskStatus.objects.none()

    def require_manage(self):
        self._require_admin()
        self._require_writable()


class _StatusWrites(_BoardSetupMixin, NamedRowWritesMixin):
    serializer_class = TaskStatusSerializer
    name_taken = gettext_lazy("A column with this name already exists in this project.")


@extend_schema(tags=["Projects - Statuses & Labels"])
@extend_schema_view(
    get=extend_schema(summary="List the columns of a project"),
    post=extend_schema(
        summary="Create a column", description="It joins the end of the board."
    ),
)
class StatusListView(_StatusWrites, ProjectContextMixin, ListCreateAPIView):
    # A project's columns, listed whole.
    pagination_class = None

    def get_queryset(self):
        return self.project.statuses.order_by("position", "created_at", "uuid")

    def perform_create(self, serializer):
        with taken_value_refused("name", self.name_taken):
            serializer.instance = create_status(
                self.project, **serializer.validated_data
            )


@extend_schema(tags=["Projects - Statuses & Labels"])
@extend_schema_view(
    get=extend_schema(summary="Get a column"),
    patch=extend_schema(summary="Update a column"),
    delete=extend_schema(
        summary="Delete a column",
        description=(
            "`move_to` names the column that takes its tasks; it is required "
            "while the column holds any."
        ),
        parameters=[StatusDeleteQuerySerializer],
    ),
)
class StatusDetailView(_StatusWrites, ProjectRowMixin, RetrieveUpdateDestroyAPIView):
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def perform_destroy(self, instance):
        query = StatusDeleteQuerySerializer(
            data=self.request.query_params, context=self.get_serializer_context()
        )
        query.is_valid(raise_exception=True)
        delete_status(
            instance, move_to=query.validated_data["move_to"], actor=self.request.user
        )


@extend_schema(
    tags=["Projects - Statuses & Labels"],
    summary="Order the columns of a project",
    description="Columns the request leaves out keep their order after the ones it lists.",
)
class StatusReorderView(_BoardSetupMixin, ProjectContextMixin, ReorderView):
    def get_queryset(self):
        return self.project.statuses.all()

    def perform_reorder(self, statuses, data):
        reorder_statuses(self.project, [status.uuid for status in statuses])
