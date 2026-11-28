from django.http import Http404, HttpResponse
from django.views.decorators.http import condition, require_GET
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from ..models import Calendar
from ..serializers import CalendarFeedSerializer
from ..services.feeds import (
    disable_feed,
    enable_feed,
    feed_etag,
    feed_path,
    render_feed,
)
from .calendars import CalendarRowsMixin


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
@extend_schema_view(
    get=extend_schema(summary="Get the address of a calendar's ICS feed"),
    post=extend_schema(
        summary="Turn the ICS feed on, or replace its address with a new one",
        request=None,
        responses={201: CalendarFeedSerializer},
    ),
    delete=extend_schema(summary="Turn the ICS feed off, revoking its address"),
)
class CalendarFeedView(CalendarRowsMixin, GenericAPIView):
    """The read-only ICS feed of a calendar the caller owns."""

    serializer_class = CalendarFeedSerializer

    def answer(self, calendar, status_code=status.HTTP_200_OK):
        path = feed_path(calendar)
        url = self.request.build_absolute_uri(path) if path else None
        return Response(self.get_serializer({"url": url}).data, status=status_code)

    def get(self, request, *args, **kwargs):
        return self.answer(self.own_calendar())

    def post(self, request, *args, **kwargs):
        calendar = self.own_calendar()
        enable_feed(calendar)
        return self.answer(calendar, status.HTTP_201_CREATED)

    def delete(self, request, *args, **kwargs):
        disable_feed(self.own_calendar())
        return Response(status=status.HTTP_204_NO_CONTENT)
