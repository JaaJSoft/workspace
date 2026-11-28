from django.http import Http404
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.generics import (
    GenericAPIView,
    ListCreateAPIView,
    RetrieveUpdateDestroyAPIView,
)
from rest_framework.response import Response

from ..filters import CalendarFilter
from ..models import Calendar
from ..queries import reachable_calendars
from ..serializers import CalendarSerializer
from ..services.calendars import (
    create_calendar,
    external_source,
    request_sync,
    update_calendar,
)


class CalendarRowsMixin:
    """The calendars the caller can see. Changing one takes owning it: a
    calendar someone subscribed to is theirs to read only."""

    queryset = Calendar.objects.none()
    serializer_class = CalendarSerializer
    lookup_field = "uuid"

    def get_queryset(self):
        return reachable_calendars(self.request.user)

    def get_object(self):
        try:
            return super().get_object()
        except Http404:
            raise NotFound(_("Calendar not found.")) from None

    def own_calendar(self):
        calendar = self.get_object()
        if calendar.owner_id != self.request.user.id:
            raise PermissionDenied(_("Only its owner can change a calendar."))
        return calendar


@extend_schema(tags=["Calendar"])
@extend_schema_view(
    get=extend_schema(
        summary="List the calendars the caller can see",
        description=(
            "Their own, the ones filled from an external ICS feed included, "
            "and the ones they subscribed to."
        ),
    ),
    post=extend_schema(
        summary="Create a calendar",
        description=(
            "With `source_url`, an external calendar: its events are read "
            "from that ICS feed, now and again from then on."
        ),
    ),
)
class CalendarListView(CalendarRowsMixin, ListCreateAPIView):
    filterset_class = CalendarFilter
    # A person's calendars, listed whole.
    pagination_class = None

    def get_queryset(self):
        return super().get_queryset().order_by("name", "uuid")

    def perform_create(self, serializer):
        serializer.instance = create_calendar(
            self.request.user,
            name=serializer.validated_data["name"],
            color=serializer.validated_data.get("color", "primary"),
            source_url=serializer.validated_data.get("source_url"),
        )


@extend_schema(tags=["Calendar"])
@extend_schema_view(
    get=extend_schema(summary="Get a calendar"),
    patch=extend_schema(summary="Rename or recolor a calendar"),
    delete=extend_schema(summary="Delete a calendar and its events"),
)
class CalendarDetailView(CalendarRowsMixin, RetrieveUpdateDestroyAPIView):
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def update(self, request, *args, **kwargs):
        calendar = self.own_calendar()
        serializer = self.get_serializer(calendar, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        update_calendar(calendar, serializer.validated_data)
        return Response(self.get_serializer(calendar).data)

    def destroy(self, request, *args, **kwargs):
        self.own_calendar().delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Calendar"])
class CalendarSyncView(CalendarRowsMixin, GenericAPIView):
    @extend_schema(
        summary="Read the feed of an external calendar again now",
        description=(
            "Answers at once; `last_synced_at` and `sync_error` of the "
            "calendar say how the reading went."
        ),
        request=None,
        responses={202: None},
    )
    def post(self, request, *args, **kwargs):
        calendar = self.own_calendar()
        if external_source(calendar) is None:
            raise PermissionDenied(_("Only an external calendar has a feed to read."))
        request_sync(calendar)
        return Response(status=status.HTTP_202_ACCEPTED)
