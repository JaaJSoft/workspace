import logging

from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Calendar
from .models_external import ExternalCalendar
from .serializers_external import (
    ExternalCalendarCreateSerializer,
    ExternalCalendarSerializer,
    ExternalCalendarUpdateSerializer,
)
from .tasks import sync_external_calendar_task

logger = logging.getLogger(__name__)


@extend_schema(tags=['Calendar'])
class ExternalCalendarListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="List user's external calendar subscriptions")
    def get(self, request):
        externals = ExternalCalendar.objects.filter(
            calendar__owner=request.user,
        ).select_related('calendar')
        return Response(ExternalCalendarSerializer(externals, many=True).data)

    @extend_schema(summary="Subscribe to an external ICS feed", request=ExternalCalendarCreateSerializer)
    @transaction.atomic
    def post(self, request):
        ser = ExternalCalendarCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        cal = Calendar.objects.create(
            name=data['name'],
            color=data['color'],
            owner=request.user,
        )
        ext = ExternalCalendar.objects.create(
            calendar=cal,
            url=data['url'],
        )
        ext = ExternalCalendar.objects.select_related('calendar').get(pk=ext.pk)

        sync_external_calendar_task.delay(str(ext.uuid))

        return Response(
            ExternalCalendarSerializer(ext).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=['Calendar'])
class ExternalCalendarDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_external(self, ext_id, user):
        try:
            return ExternalCalendar.objects.select_related('calendar').get(
                uuid=ext_id, calendar__owner=user,
            )
        except ExternalCalendar.DoesNotExist:
            return None

    @extend_schema(summary="Update an external calendar", request=ExternalCalendarUpdateSerializer)
    def put(self, request, ext_id):
        ext = self._get_external(ext_id, request.user)
        if not ext:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        ser = ExternalCalendarUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        cal = ext.calendar
        for field in ('name', 'color'):
            if field in ser.validated_data:
                setattr(cal, field, ser.validated_data[field])
        cal.save()

        ext = ExternalCalendar.objects.select_related('calendar').get(pk=ext.pk)
        return Response(ExternalCalendarSerializer(ext).data)

    @extend_schema(summary="Delete an external calendar subscription")
    def delete(self, request, ext_id):
        ext = self._get_external(ext_id, request.user)
        if not ext:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        ext.calendar.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['Calendar'])
class ExternalCalendarSyncView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Trigger a manual sync for an external calendar")
    def post(self, request, ext_id):
        try:
            ext = ExternalCalendar.objects.get(
                uuid=ext_id, calendar__owner=request.user,
            )
        except ExternalCalendar.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        sync_external_calendar_task.delay(str(ext.uuid))
        return Response({'detail': 'Sync started.'}, status=status.HTTP_202_ACCEPTED)
