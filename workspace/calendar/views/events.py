import logging
from datetime import UTC, datetime

from dateutil.parser import parse as dateutil_parse
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import OuterRef, Prefetch, Q, Subquery
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin
from workspace.notifications.services.notifications import (
    mark_source_read,
    mark_sources_read,
    notify_many,
)

from ..models import Calendar, Event, EventMember
from ..queries import member_event_ids, visible_calendar_ids
from ..recurrence import expand_recurring_events, make_virtual_occurrence
from ..serializers import (
    EventCreateSerializer,
    EventRespondSerializer,
    EventSerializer,
    EventUpdateSerializer,
)
from ..services.event_scope import (
    EventScopeError,
    cancel_event,
    is_external_calendar,
    update_event,
)
from ..services.invitations import NotInvitedError, respond_to_invitation
from ..services.timezones import current_timezone_name
from ..upcoming import get_upcoming_page


def _mark_displayed_events_read(user, event_pks):
    """Displayed events are demonstrably seen - settle their notifications.

    A recurring occurrence's synthetic ``<master>:<start>`` id resolves to
    the master row, which is also what the notification cron keys on.
    """
    pks = {str(pk).split(":", 1)[0] for pk in event_pks if pk}
    if pks:
        mark_sources_read(user, [Event(pk=pk) for pk in pks])


def _parse_dt(value):
    """Parse datetime string, handling URL-encoded timezone offsets."""
    if not value:
        return None
    try:
        from django.utils import timezone

        dt = dateutil_parse(value)
        if dt.tzinfo is None:
            dt = timezone.make_aware(dt)
        return dt
    except ValueError, TypeError:
        return None


def _sort_instant(value):
    """Comparable UTC instant for a serialized start value.

    Date-only all-day labels parse as midnight in the active (user)
    timezone, so an all-day event sorts at the top of that user-local day,
    ahead of its timed events, regardless of the offsets in the strings.
    """
    dt = _parse_dt(value)
    if dt is None:
        return datetime.min.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


User = get_user_model()
logger = logging.getLogger(__name__)


def _prefetch_event(qs):
    from workspace.calendar.models import Poll

    return (
        qs.prefetch_related(
            Prefetch(
                "members",
                queryset=EventMember.objects.select_related("user"),
            ),
        )
        .select_related("owner", "calendar")
        .annotate(
            _poll_id=Subquery(
                Poll.objects.filter(event=OuterRef("pk")).values("uuid")[:1]
            ),
        )
    )


@extend_schema(tags=["Calendar"])
class EventListView(CacheControlMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="List events (range mode) or next N upcoming events (cursor mode)",
        parameters=[
            OpenApiParameter(
                name="start",
                type=str,
                required=False,
                description="Range mode: start of window",
            ),
            OpenApiParameter(
                name="end",
                type=str,
                required=False,
                description="Range mode: end of window",
            ),
            OpenApiParameter(
                name="after",
                type=str,
                required=False,
                description="Cursor mode: return events starting at or after this datetime",
            ),
            OpenApiParameter(
                name="limit",
                type=int,
                required=False,
                description="Cursor mode: max events per page (default 20, max 100)",
            ),
            OpenApiParameter(
                name="calendar_ids",
                type=str,
                required=False,
                description="Comma-separated calendar UUIDs",
            ),
            OpenApiParameter(
                name="show_declined",
                type=bool,
                required=False,
                description="Cursor mode: include declined events",
            ),
        ],
    )
    def get(self, request):
        after_param = request.query_params.get("after")
        if after_param is not None and after_param.strip():
            return self._get_cursor(request, after_param)
        return self._get_range(request)

    def _get_cursor(self, request, after_param):
        after = _parse_dt(after_param)
        if after is None:
            return Response(
                {"detail": 'Invalid "after" datetime.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            limit = int(request.query_params.get("limit", 20))
        except TypeError, ValueError:
            limit = 20
        limit = max(1, min(limit, 100))

        calendar_ids_param = request.query_params.get("calendar_ids")
        calendar_ids = None
        if calendar_ids_param is not None:
            calendar_ids = [
                c.strip() for c in calendar_ids_param.split(",") if c.strip()
            ]

        show_declined = request.query_params.get("show_declined", "").lower() in (
            "1",
            "true",
            "yes",
        )

        events, next_after = get_upcoming_page(
            user=request.user,
            after=after,
            limit=limit,
            calendar_ids=calendar_ids,
            show_declined=show_declined,
        )
        _mark_displayed_events_read(request.user, [e.get("uuid") for e in events])
        return Response({"events": events, "next_after": next_after})

    def _get_range(self, request):
        start = request.query_params.get("start")
        end = request.query_params.get("end")
        if not start or not end:
            return Response(
                {"detail": 'Both "start" and "end" query parameters are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        range_start = _parse_dt(start)
        range_end = _parse_dt(end)
        if range_start is None or range_end is None:
            return Response(
                {"detail": "Invalid start or end datetime."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = request.user

        # Filter by specific calendars or all visible
        calendar_ids_param = request.query_params.get("calendar_ids")
        if calendar_ids_param is not None:
            cal_ids = [c.strip() for c in calendar_ids_param.split(",") if c.strip()]
        else:
            cal_ids = visible_calendar_ids(user)

        # Membership goes through the id subquery, not the members__user
        # join: ORing a joined branch with calendar_id__in blocks per-branch
        # index use, and the join fan-out is what forced distinct() here.
        cal_or_member = Q(calendar_id__in=cal_ids) | Q(uuid__in=member_event_ids(user))

        # Non-recurring events (exclude exceptions)
        non_recurring = Event.objects.filter(
            cal_or_member,
            recurrence_frequency__isnull=True,
            recurrence_parent__isnull=True,
            is_cancelled=False,
            start__lt=range_end,
        ).filter(
            Q(end__gt=range_start) | Q(end__isnull=True, start__gte=range_start),
        )
        non_recurring = _prefetch_event(non_recurring).order_by("start")

        non_recurring_data = EventSerializer(non_recurring, many=True).data

        # Recurring masters overlapping the range
        masters = Event.objects.filter(
            cal_or_member,
            recurrence_frequency__isnull=False,
            recurrence_parent__isnull=True,
            start__lt=range_end,
        ).filter(
            Q(recurrence_end__isnull=True) | Q(recurrence_end__gt=range_start),
        )
        masters = _prefetch_event(masters)

        recurring_data = expand_recurring_events(masters, range_start, range_end)

        # Merge and sort as instants: values mix date-only all-day labels
        # with ISO datetimes whose offsets can differ, so a plain string
        # sort would misorder them.
        all_events = non_recurring_data + recurring_data
        all_events.sort(key=lambda e: _sort_instant(e.get("start")))
        _mark_displayed_events_read(request.user, [e.get("uuid") for e in all_events])
        return Response(all_events)

    @extend_schema(summary="Create an event", request=EventCreateSerializer)
    @transaction.atomic
    def post(self, request):
        ser = EventCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        # Validate calendar ownership
        try:
            cal = Calendar.objects.get(pk=data["calendar_id"], owner=request.user)
        except Calendar.DoesNotExist:
            return Response(
                {"detail": "Calendar not found or not owned by you."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if is_external_calendar(cal.pk):
            return Response(
                {"detail": "Cannot create events in an external calendar."},
                status=status.HTTP_403_FORBIDDEN,
            )

        event = Event.objects.create(
            calendar=cal,
            title=data["title"],
            description=data["description"],
            start=data["start"],
            end=data["end"],
            all_day=data["all_day"],
            # All-day events are zone-less day labels; timed events anchor
            # their wall clock in the creator's active timezone.
            timezone="" if data["all_day"] else current_timezone_name(),
            location=data["location"],
            owner=request.user,
            recurrence_frequency=data.get("recurrence_frequency"),
            recurrence_interval=data.get("recurrence_interval", 1),
            recurrence_end=data.get("recurrence_end"),
        )

        member_ids = data.get("member_ids", [])
        if member_ids:
            users = list(
                User.objects.filter(id__in=member_ids).exclude(id=request.user.id)
            )
            EventMember.objects.bulk_create(
                [EventMember(event=event, user=u) for u in users]
            )
            notify_many(
                recipients=users,
                origin="calendar",
                title=f'Invited to "{event.title}"',
                body=f"{request.user.username} invited you to an event.",
                url=f"/calendar?event={event.pk}",
                actor=request.user,
                source=event,
            )

        event = _prefetch_event(Event.objects.filter(pk=event.pk)).first()
        return Response(EventSerializer(event).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Calendar"])
class EventDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_event(self, event_id, user):
        event = _prefetch_event(Event.objects.filter(pk=event_id)).first()
        if not event:
            return None, Response(
                {"detail": "Event not found."}, status=status.HTTP_404_NOT_FOUND
            )
        # Members are already prefetched by _prefetch_event; iterate the
        # cached list instead of issuing a redundant .filter().exists().
        is_member = any(m.user_id == user.id for m in event.members.all())
        cal_ids = visible_calendar_ids(user)
        if event.calendar_id not in cal_ids and not is_member:
            return None, Response(
                {"detail": "No access."}, status=status.HTTP_403_FORBIDDEN
            )
        return event, None

    @extend_schema(summary="Get event detail")
    def get(self, request, event_id):
        event, err = self._get_event(event_id, request.user)
        if err:
            return err

        mark_source_read(request.user, event)

        # If original_start is provided, return the specific occurrence
        original_start_str = request.query_params.get("original_start")
        if original_start_str and event.is_recurring:
            original_start = _parse_dt(original_start_str)
            if original_start:
                # Single prefetched query: the serializer below needs the
                # members/calendar relations _prefetch_event attaches.
                exc = _prefetch_event(
                    Event.objects.filter(
                        recurrence_parent=event,
                        original_start=original_start,
                    )
                ).first()
                if exc:
                    return Response(EventSerializer(exc).data)
                # Build virtual occurrence
                occ = make_virtual_occurrence(event, original_start)
                return Response(occ)

        return Response(EventSerializer(event).data)

    @extend_schema(summary="Update an event", request=EventUpdateSerializer)
    def put(self, request, event_id):
        event, err = self._get_event(event_id, request.user)
        if err:
            return err
        if event.owner_id != request.user.id:
            return Response(
                {"detail": "Only the owner can edit."}, status=status.HTTP_403_FORBIDDEN
            )
        if is_external_calendar(event.calendar_id):
            return Response(
                {"detail": "Cannot edit events from an external calendar."},
                status=status.HTTP_403_FORBIDDEN,
            )

        ser = EventUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        scope = data.pop("scope", "all")
        original_start = data.pop("original_start", None)

        try:
            written = update_event(
                event, data, request.user, scope=scope, original_start=original_start
            )
        except EventScopeError as exc:
            return Response({"detail": exc.detail}, status=exc.status_code)

        written = _prefetch_event(Event.objects.filter(pk=written.pk)).first()
        return Response(EventSerializer(written).data)

    @extend_schema(summary="Delete an event")
    def delete(self, request, event_id):
        event, err = self._get_event(event_id, request.user)
        if err:
            return err
        if event.owner_id != request.user.id:
            return Response(
                {"detail": "Only the owner can delete."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if is_external_calendar(event.calendar_id):
            return Response(
                {"detail": "Cannot delete events from an external calendar."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            cancel_event(
                event,
                request.user,
                scope=request.query_params.get("scope", "all"),
                original_start=_parse_dt(request.query_params.get("original_start")),
            )
        except EventScopeError as exc:
            return Response({"detail": exc.detail}, status=exc.status_code)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Calendar"])
class EventRespondView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Respond to an invitation", request=EventRespondSerializer)
    def post(self, request, event_id):
        ser = EventRespondSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            membership, _event = respond_to_invitation(
                event_id, request.user, ser.validated_data["status"]
            )
        except NotInvitedError:
            return Response(
                {"detail": "Not invited."}, status=status.HTTP_403_FORBIDDEN
            )
        return Response({"status": membership.status})
