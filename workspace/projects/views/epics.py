from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView

from ..models import Epic
from ..serializers import EpicSerializer
from ..services.epics import epics_with_progress
from .context import (
    ManagedRowsMixin,
    NamedRowWritesMixin,
    ProjectContextMixin,
    ProjectRowMixin,
)


class _EpicWrites(ManagedRowsMixin, NamedRowWritesMixin):
    queryset = Epic.objects.none()
    serializer_class = EpicSerializer
    manage_action = "manage_epics"
    name_taken = gettext_lazy("An epic with this name already exists in this project.")

    def get_queryset(self):
        return epics_with_progress(self.project)


@extend_schema(tags=["Projects - Epics"])
@extend_schema_view(
    get=extend_schema(summary="List the epics of a project"),
    post=extend_schema(summary="Create an epic"),
)
class EpicListView(_EpicWrites, ProjectContextMixin, ListCreateAPIView):
    # A project's epics, listed whole.
    pagination_class = None


@extend_schema(tags=["Projects - Epics"])
@extend_schema_view(
    get=extend_schema(summary="Get an epic"),
    patch=extend_schema(summary="Update an epic"),
    delete=extend_schema(summary="Delete an epic"),
)
class EpicDetailView(_EpicWrites, ProjectRowMixin, RetrieveUpdateDestroyAPIView):
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def get_object(self):
        # With its progress counts, which the serializer reads.
        return get_object_or_404(self.get_queryset(), pk=self.row.pk)
