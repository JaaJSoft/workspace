from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.cache import cached_response, invalidate
from workspace.common.mixins import CacheControlMixin

from ..models import Calendar
from ..queries import visible_calendars
from ..serializers import CalendarCreateSerializer, CalendarSerializer


@extend_schema(tags=["Calendar"])
class CalendarListView(CacheControlMixin, APIView):
    permission_classes = [IsAuthenticated]
    cache_max_age = 300

    @extend_schema(summary="List user's calendars (owned + subscribed)")
    @cached_response(300)
    def get(self, request):
        owned, subscribed = visible_calendars(request.user)

        return Response(
            {
                "owned": CalendarSerializer(owned, many=True).data,
                "subscribed": CalendarSerializer(subscribed, many=True).data,
            }
        )

    @extend_schema(summary="Create a calendar", request=CalendarCreateSerializer)
    def post(self, request):
        ser = CalendarCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        cal = Calendar.objects.create(owner=request.user, **ser.validated_data)
        cal = Calendar.objects.select_related("owner").get(pk=cal.pk)
        invalidate("CalendarListView", user=request.user)
        return Response(CalendarSerializer(cal).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Calendar"])
class CalendarDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Update a calendar", request=CalendarCreateSerializer)
    def put(self, request, calendar_id):
        try:
            cal = Calendar.objects.get(pk=calendar_id, owner=request.user)
        except Calendar.DoesNotExist:
            return Response(
                {"detail": "Calendar not found."}, status=status.HTTP_404_NOT_FOUND
            )

        ser = CalendarCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        for k, v in ser.validated_data.items():
            setattr(cal, k, v)
        cal.save()
        cal = Calendar.objects.select_related("owner").get(pk=cal.pk)
        invalidate("CalendarListView", user=request.user)
        return Response(CalendarSerializer(cal).data)

    @extend_schema(summary="Delete a calendar")
    def delete(self, request, calendar_id):
        try:
            cal = Calendar.objects.get(pk=calendar_id, owner=request.user)
        except Calendar.DoesNotExist:
            return Response(
                {"detail": "Calendar not found."}, status=status.HTTP_404_NOT_FOUND
            )
        cal.delete()
        invalidate("CalendarListView", user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
