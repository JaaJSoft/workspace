from django.utils.dateparse import parse_date, parse_datetime
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from ..queries import tasks_due_between
from ..serializers import TaskCalendarSerializer

# No calendar view paints more than a year at once, and the result set is
# bounded only by the window: a wider range is a client bug, not a request
# worth serving.
MAX_RANGE_DAYS = 366


def _parse_day(value):
    """Read a calendar boundary as a plain date.

    FullCalendar sends the grid's own offset (``2026-07-27T00:00:00+02:00``),
    so the date part is the day the user actually sees - normalizing to UTC
    first would shift the window by one day for half the world. Bare dates
    are accepted too, for callers that aren't the calendar UI.
    """
    if not value:
        return None
    try:
        parsed = parse_date(value)
        if parsed is not None:
            return parsed
        dt = parse_datetime(value)
    except ValueError:
        # Both parsers raise (rather than returning None) on a value that is
        # well-formed but impossible - 2026-13-01, Feb 30th, hour 25. Letting
        # that escape would turn a bad query string into a 500.
        return None
    return dt.date() if dt is not None else None


@extend_schema(
    tags=["Projects - Tasks"],
    summary="List task due dates in a date range",
    description=(
        "Return open tasks whose due date falls in [start, end) across every "
        "project the user can access. Read-only overlay for the calendar UI: "
        "completed tasks and archived projects are excluded."
    ),
    parameters=[
        OpenApiParameter(
            name="start",
            type=str,
            required=True,
            description="Inclusive start of the window (date or ISO datetime)",
        ),
        OpenApiParameter(
            name="end",
            type=str,
            required=True,
            description="Exclusive end of the window (date or ISO datetime)",
        ),
    ],
    responses={
        200: OpenApiResponse(
            response=TaskCalendarSerializer(many=True),
            description="Tasks due in the window, earliest first.",
        ),
        400: OpenApiResponse(
            response=OpenApiTypes.OBJECT, description="Invalid range."
        ),
    },
)
class TaskCalendarView(APIView):
    def get(self, request):
        start = _parse_day(request.query_params.get("start"))
        end = _parse_day(request.query_params.get("end"))
        if start is None or end is None:
            return Response(
                {"detail": 'Both "start" and "end" query parameters are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if end <= start:
            return Response(
                {"detail": '"end" must be after "start".'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if (end - start).days > MAX_RANGE_DAYS:
            return Response(
                {"detail": f"Range must not exceed {MAX_RANGE_DAYS} days."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tasks = tasks_due_between(request.user, start, end)
        return Response(TaskCalendarSerializer(tasks, many=True).data)
