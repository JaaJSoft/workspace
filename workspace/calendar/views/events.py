from django.utils import timezone
from django.utils.translation import gettext as _
from django_filters.utils import translate_validation
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.generics import GenericAPIView, ListCreateAPIView
from rest_framework.response import Response

from workspace.common.views import LinkView
from workspace.notifications.services.notifications import mark_sources_read

from ..filters import EventFilter
from ..models import Event, EventMember
from ..serializers import (
    EventResponseSerializer,
    EventSerializer,
    EventWriteSerializer,
    OccurrenceQuerySerializer,
    SeriesWriteQuerySerializer,
)
from ..services.event_creation import create_event
from ..services.event_listing import (
    EventListing,
    find_occurrence,
    occurrence_answer,
    with_details,
)
from ..services.event_scope import (
    EventScopeError,
    assert_writable,
    cancel_event,
    update_event,
)
from ..services.invitations import respond_to_invitation, withdraw_response


def _refusal(error):
    """The API error a refusal of the scope service stands for."""
    if error.status_code == status.HTTP_403_FORBIDDEN:
        return PermissionDenied(error.detail)
    return ValidationError({"detail": error.detail})


def _answer(event, context):
    """*event* as the API answers it, read again with what that takes."""
    row = with_details(Event.objects.filter(pk=event.pk)).get()
    return EventSerializer(row, context=context).data


@extend_schema(tags=["Calendar"])
@extend_schema_view(
    get=extend_schema(
        summary="List events, recurring ones as their occurrences",
        description=(
            "In the order of an agenda: by start, an all-day event at the "
            "start of its day. With `start` and `end`, the events "
            "overlapping that window; with `start` alone, the ones starting "
            "from it on, a list that has no end while a series repeats: it "
            "cannot be counted, and is read ten thousand events deep at "
            "most, after which `start` has to move forward. An occurrence "
            "carries the uuid of its series and its `original_start`."
        ),
    ),
    post=extend_schema(
        summary="Create an event",
        request=EventWriteSerializer,
        responses={201: EventSerializer},
    ),
)
class EventListView(ListCreateAPIView):
    queryset = Event.objects.none()
    serializer_class = EventSerializer
    filterset_class = EventFilter

    def list(self, request, *args, **kwargs):
        asked = EventFilter(
            request.query_params, queryset=self.queryset, request=request
        )
        if not asked.is_valid():
            raise translate_validation(asked.errors)
        listing = EventListing(
            request.user,
            start=asked.form.cleaned_data["start"] or timezone.now(),
            end=asked.form.cleaned_data["end"],
            narrow=asked.filter_queryset,
            context=self.get_serializer_context(),
        )
        page = self.paginate_queryset(listing)
        # Displayed events are demonstrably seen: settle their notifications.
        mark_sources_read(
            request.user, [Event(pk=pk) for pk in {row.row_id for row in page}]
        )
        return self.get_paginated_response([row.data for row in page])

    def create(self, request, *args, **kwargs):
        body = EventWriteSerializer(
            data=request.data, context=self.get_serializer_context()
        )
        body.is_valid(raise_exception=True)
        data = body.validated_data
        event = create_event(
            request.user,
            calendar=data["calendar"],
            title=data["title"],
            start=data["start"],
            end=data.get("end"),
            all_day=data.get("all_day", False),
            description=data.get("description", ""),
            location=data.get("location", ""),
            rule=data.get("recurrence_rule", ""),
            member_ids=data.get("members", ()),
            guests=data.get("guests", ()),
        )
        return Response(
            _answer(event, self.get_serializer_context()),
            status=status.HTTP_201_CREATED,
        )


class OccurrenceMixin:
    """An event the caller can see, or with ``original_start`` one occurrence
    of it. One they cannot see answers like one that does not exist."""

    queryset = Event.objects.none()
    serializer_class = EventSerializer
    query_serializer = OccurrenceQuerySerializer

    def read_query(self, query_serializer=None):
        asked = (query_serializer or self.query_serializer)(
            data=self.request.query_params
        )
        asked.is_valid(raise_exception=True)
        return asked.validated_data

    def find(self, asked):
        occurrence = find_occurrence(
            self.request.user, self.kwargs["uuid"], asked.get("original_start")
        )
        if occurrence is None:
            raise NotFound(_("Event not found."))
        return occurrence


@extend_schema(tags=["Calendar"])
@extend_schema_view(
    get=extend_schema(
        summary="Get an event, or one occurrence of a recurring one",
        parameters=[OccurrenceQuerySerializer],
    ),
    patch=extend_schema(
        summary="Update an event, or part of a recurring one",
        description=(
            "Answers the event written: the occurrence for `this`, the "
            "series that now starts there for `future`."
        ),
        parameters=[SeriesWriteQuerySerializer],
        request=EventWriteSerializer,
    ),
    delete=extend_schema(
        summary="Delete an event, or part of a recurring one",
        parameters=[SeriesWriteQuerySerializer],
    ),
)
class EventDetailView(OccurrenceMixin, GenericAPIView):
    def writable(self, occurrence):
        try:
            assert_writable(occurrence.series, self.request.user)
        except EventScopeError as error:
            raise _refusal(error) from None

    def part_asked(self, occurrence, asked):
        """Which occurrences of the series a write is about, and from which
        slot. Never guessed: taking a missing answer for the whole series
        would turn "skip this Monday" into deleting the weekly meeting."""
        if not occurrence.series.is_recurring:
            return "all", None
        part = asked.get("occurrences")
        if part is None:
            raise ValidationError(
                {"occurrences": [_("Say which occurrences of the series are meant.")]}
            )
        if part != "all" and occurrence.original_start is None:
            raise ValidationError(
                {"original_start": [_("Say which occurrence is meant.")]}
            )
        return part, occurrence.original_start

    def get(self, request, *args, **kwargs):
        occurrence = self.find(self.read_query())
        mark_sources_read(request.user, {occurrence.series, occurrence.row})
        return Response(occurrence_answer(occurrence, self.get_serializer_context()))

    def patch(self, request, *args, **kwargs):
        asked = self.read_query(SeriesWriteQuerySerializer)
        occurrence = self.find(asked)
        self.writable(occurrence)
        body = EventWriteSerializer(
            data=request.data, partial=True, context=self.get_serializer_context()
        )
        body.is_valid(raise_exception=True)
        part, original_start = self.part_asked(occurrence, asked)
        # The names the scope service knows these two by.
        data = dict(body.validated_data)
        if "calendar" in data:
            data["calendar_id"] = data.pop("calendar").pk
        if "members" in data:
            data["member_ids"] = data.pop("members")
        try:
            written = update_event(
                occurrence.series,
                data,
                request.user,
                scope=part,
                original_start=original_start,
            )
        except EventScopeError as error:
            raise _refusal(error) from None
        return Response(_answer(written, self.get_serializer_context()))

    def delete(self, request, *args, **kwargs):
        asked = self.read_query(SeriesWriteQuerySerializer)
        occurrence = self.find(asked)
        self.writable(occurrence)
        part, original_start = self.part_asked(occurrence, asked)
        try:
            cancel_event(
                occurrence.series,
                request.user,
                scope=part,
                original_start=original_start,
            )
        except EventScopeError as error:
            raise _refusal(error) from None
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=["Calendar"],
    parameters=[OccurrenceQuerySerializer],
    description=(
        "With `original_start`, the answer is for that occurrence when it "
        "was changed on its own and carries its own invitees; for the series "
        "otherwise."
    ),
)
@extend_schema_view(
    put=extend_schema(summary="Accept or decline an invitation"),
    delete=extend_schema(summary="Take an answer back: the invitation is pending"),
)
class EventResponseView(OccurrenceMixin, LinkView):
    serializer_class = EventResponseSerializer

    def get_object(self):
        occurrence = self.find(self.read_query())
        invitations = {
            invitation.event_id: invitation
            for invitation in EventMember.objects.filter(
                event__in={occurrence.series, occurrence.row}, user=self.request.user
            )
        }
        invitation = invitations.get(occurrence.row.pk) or invitations.get(
            occurrence.series.pk
        )
        if invitation is None:
            raise PermissionDenied(_("Not invited."))
        return invitation

    def perform_link(self, invitation, data):
        respond_to_invitation(invitation.event_id, self.request.user, data["status"])

    def perform_unlink(self, invitation):
        withdraw_response(invitation.event_id, self.request.user)
