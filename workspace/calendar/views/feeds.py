from django.http import Http404, HttpResponse
from django.views.decorators.http import condition, require_GET
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import Calendar
from ..services.feeds import (
    disable_feed,
    enable_feed,
    feed_etag,
    feed_path,
    render_feed,
)


def _calendar_for_token(token):
    calendar = Calendar.objects.filter(feed_token=token).first() if token else None
    if calendar is None:
        raise Http404
    return calendar


@require_GET
@condition(etag_func=lambda request, token: feed_etag(_calendar_for_token(token)))
def calendar_feed(request, token):
    """The public ICS feed. The token in the URL is the only credential."""
    calendar = _calendar_for_token(token)
    response = HttpResponse(
        render_feed(calendar), content_type="text/calendar; charset=utf-8"
    )
    response["Content-Disposition"] = 'inline; filename="calendar.ics"'
    response["Cache-Control"] = "no-cache"
    response["X-Robots-Tag"] = "noindex"
    return response


@extend_schema(tags=["Calendar"])
class CalendarFeedView(APIView):
    """The read-only ICS feed URL of a calendar the user owns."""

    permission_classes = [IsAuthenticated]

    def _calendar(self, request, calendar_id):
        return Calendar.objects.filter(pk=calendar_id, owner=request.user).first()

    def _payload(self, request, calendar):
        path = feed_path(calendar)
        return {"url": request.build_absolute_uri(path) if path else None}

    @extend_schema(summary="Get the calendar's ICS feed URL (null while off)")
    def get(self, request, calendar_id):
        calendar = self._calendar(request, calendar_id)
        if calendar is None:
            return Response(
                {"detail": "Calendar not found."}, status=status.HTTP_404_NOT_FOUND
            )
        return Response(self._payload(request, calendar))

    @extend_schema(summary="Turn the ICS feed on, or replace its URL with a new one")
    def post(self, request, calendar_id):
        calendar = self._calendar(request, calendar_id)
        if calendar is None:
            return Response(
                {"detail": "Calendar not found."}, status=status.HTTP_404_NOT_FOUND
            )
        enable_feed(calendar)
        return Response(
            self._payload(request, calendar), status=status.HTTP_201_CREATED
        )

    @extend_schema(summary="Turn the ICS feed off, revoking its URL")
    def delete(self, request, calendar_id):
        calendar = self._calendar(request, calendar_id)
        if calendar is None:
            return Response(
                {"detail": "Calendar not found."}, status=status.HTTP_404_NOT_FOUND
            )
        disable_feed(calendar)
        return Response(status=status.HTTP_204_NO_CONTENT)
