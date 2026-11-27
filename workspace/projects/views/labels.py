from django.utils.translation import gettext_lazy
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView

from ..models import Label
from ..serializers import LabelSerializer
from .context import (
    ManagedRowsMixin,
    NamedRowWritesMixin,
    ProjectContextMixin,
    ProjectRowMixin,
)


class _LabelWrites(ManagedRowsMixin, NamedRowWritesMixin):
    queryset = Label.objects.none()
    serializer_class = LabelSerializer
    manage_action = "manage_labels"
    name_taken = gettext_lazy("A label with this name already exists in this project.")


@extend_schema(tags=["Projects - Statuses & Labels"])
@extend_schema_view(
    get=extend_schema(summary="List the labels of a project"),
    post=extend_schema(summary="Create a label"),
)
class LabelListView(_LabelWrites, ProjectContextMixin, ListCreateAPIView):
    # A project's labels, listed whole.
    pagination_class = None

    def get_queryset(self):
        return self.project.labels.order_by("name", "uuid")


@extend_schema(tags=["Projects - Statuses & Labels"])
@extend_schema_view(
    get=extend_schema(summary="Get a label"),
    patch=extend_schema(summary="Update a label"),
    delete=extend_schema(summary="Delete a label"),
)
class LabelDetailView(_LabelWrites, ProjectRowMixin, RetrieveUpdateDestroyAPIView):
    http_method_names = ["get", "patch", "delete", "head", "options"]
