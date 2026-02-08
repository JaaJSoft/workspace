from django.contrib.auth import get_user_model
from django.db.models import Q, Prefetch
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Calendar, CalendarSubscription, Event, EventMember
from .serializers import (
    CalendarCreateSerializer,
    CalendarSerializer,
    EventCreateSerializer,
    EventRespondSerializer,
    EventSerializer,
    EventUpdateSerializer,
)

User = get_user_model()


def _prefetch_event(qs):
    return qs.prefetch_related(
        Prefetch(
            'members',
            queryset=EventMember.objects.select_related('user'),
        ),
    ).select_related('owner', 'calendar')


def _visible_calendar_ids(user):
    """Calendar IDs the user can see: owned + subscribed."""
    owned = Calendar.objects.filter(owner=user).values_list('uuid', flat=True)
    subscribed = CalendarSubscription.objects.filter(user=user).values_list('calendar_id', flat=True)
    return list(owned) + list(subscribed)


# ---------- Calendar CRUD ----------

@extend_schema(tags=['Calendar'])
class CalendarListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="List user's calendars (owned + subscribed)")
    def get(self, request):
        owned = Calendar.objects.filter(owner=request.user).select_related('owner')
        sub_ids = CalendarSubscription.objects.filter(
            user=request.user,
        ).values_list('calendar_id', flat=True)
        subscribed = Calendar.objects.filter(uuid__in=sub_ids).select_related('owner')

        return Response({
            'owned': CalendarSerializer(owned, many=True).data,
            'subscribed': CalendarSerializer(subscribed, many=True).data,
        })

    @extend_schema(summary="Create a calendar", request=CalendarCreateSerializer)
    def post(self, request):
        ser = CalendarCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        cal = Calendar.objects.create(owner=request.user, **ser.validated_data)
        cal = Calendar.objects.select_related('owner').get(pk=cal.pk)
        return Response(CalendarSerializer(cal).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=['Calendar'])
class CalendarDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Update a calendar")
    def put(self, request, calendar_id):
        try:
            cal = Calendar.objects.get(pk=calendar_id, owner=request.user)
        except Calendar.DoesNotExist:
            return Response({'detail': 'Calendar not found.'}, status=status.HTTP_404_NOT_FOUND)

        ser = CalendarCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        for k, v in ser.validated_data.items():
            setattr(cal, k, v)
        cal.save()
        cal = Calendar.objects.select_related('owner').get(pk=cal.pk)
        return Response(CalendarSerializer(cal).data)

    @extend_schema(summary="Delete a calendar")
    def delete(self, request, calendar_id):
        try:
            cal = Calendar.objects.get(pk=calendar_id, owner=request.user)
        except Calendar.DoesNotExist:
            return Response({'detail': 'Calendar not found.'}, status=status.HTTP_404_NOT_FOUND)
        cal.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------- Event CRUD ----------

@extend_schema(tags=['Calendar'])
class EventListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="List events in a date range",
        parameters=[
            OpenApiParameter(name='start', type=str, required=True),
            OpenApiParameter(name='end', type=str, required=True),
            OpenApiParameter(name='calendar_ids', type=str, required=False, description='Comma-separated calendar UUIDs'),
        ],
    )
    def get(self, request):
        start = request.query_params.get('start')
        end = request.query_params.get('end')
        if not start or not end:
            return Response(
                {'detail': 'Both "start" and "end" query parameters are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = request.user

        # Filter by specific calendars or all visible
        calendar_ids_param = request.query_params.get('calendar_ids')
        if calendar_ids_param is not None:
            cal_ids = [c.strip() for c in calendar_ids_param.split(',') if c.strip()]
        else:
            cal_ids = _visible_calendar_ids(user)

        events = Event.objects.filter(
            Q(calendar_id__in=cal_ids) | Q(members__user=user),
            start__lt=end,
        ).filter(
            Q(end__gt=start) | Q(end__isnull=True, start__gte=start),
        ).distinct()

        events = _prefetch_event(events).order_by('start')
        return Response(EventSerializer(events, many=True).data)

    @extend_schema(summary="Create an event", request=EventCreateSerializer)
    def post(self, request):
        ser = EventCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        # Validate calendar ownership
        try:
            cal = Calendar.objects.get(pk=data['calendar_id'], owner=request.user)
        except Calendar.DoesNotExist:
            return Response(
                {'detail': 'Calendar not found or not owned by you.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        event = Event.objects.create(
            calendar=cal,
            title=data['title'],
            description=data['description'],
            start=data['start'],
            end=data['end'],
            all_day=data['all_day'],
            location=data['location'],
            owner=request.user,
        )

        member_ids = data.get('member_ids', [])
        if member_ids:
            users = User.objects.filter(id__in=member_ids).exclude(id=request.user.id)
            EventMember.objects.bulk_create([
                EventMember(event=event, user=u) for u in users
            ])

        event = _prefetch_event(Event.objects.filter(pk=event.pk)).first()
        return Response(EventSerializer(event).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=['Calendar'])
class EventDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_event(self, event_id, user):
        event = _prefetch_event(Event.objects.filter(pk=event_id)).first()
        if not event:
            return None, Response({'detail': 'Event not found.'}, status=status.HTTP_404_NOT_FOUND)
        is_member = event.members.filter(user=user).exists()
        cal_ids = _visible_calendar_ids(user)
        if event.calendar_id not in cal_ids and not is_member:
            return None, Response({'detail': 'No access.'}, status=status.HTTP_403_FORBIDDEN)
        return event, None

    @extend_schema(summary="Get event detail")
    def get(self, request, event_id):
        event, err = self._get_event(event_id, request.user)
        if err:
            return err
        return Response(EventSerializer(event).data)

    @extend_schema(summary="Update an event", request=EventUpdateSerializer)
    def put(self, request, event_id):
        event, err = self._get_event(event_id, request.user)
        if err:
            return err
        if event.owner_id != request.user.id:
            return Response({'detail': 'Only the owner can edit.'}, status=status.HTTP_403_FORBIDDEN)

        ser = EventUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        if 'calendar_id' in data:
            try:
                cal = Calendar.objects.get(pk=data['calendar_id'], owner=request.user)
                event.calendar = cal
            except Calendar.DoesNotExist:
                return Response({'detail': 'Calendar not found.'}, status=status.HTTP_400_BAD_REQUEST)

        for field in ['title', 'description', 'start', 'end', 'all_day', 'location']:
            if field in data:
                setattr(event, field, data[field])
        event.save()

        if 'member_ids' in data:
            current = set(event.members.values_list('user_id', flat=True))
            new_ids = set(data['member_ids']) - {request.user.id}
            to_remove = current - new_ids
            if to_remove:
                EventMember.objects.filter(event=event, user_id__in=to_remove).delete()
            to_add = new_ids - current
            if to_add:
                users = User.objects.filter(id__in=to_add)
                EventMember.objects.bulk_create([EventMember(event=event, user=u) for u in users])

        event = _prefetch_event(Event.objects.filter(pk=event.pk)).first()
        return Response(EventSerializer(event).data)

    @extend_schema(summary="Delete an event")
    def delete(self, request, event_id):
        event, err = self._get_event(event_id, request.user)
        if err:
            return err
        if event.owner_id != request.user.id:
            return Response({'detail': 'Only the owner can delete.'}, status=status.HTTP_403_FORBIDDEN)
        event.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['Calendar'])
class EventRespondView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Respond to an invitation", request=EventRespondSerializer)
    def post(self, request, event_id):
        ser = EventRespondSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        membership = EventMember.objects.filter(event_id=event_id, user=request.user).first()
        if not membership:
            return Response({'detail': 'Not invited.'}, status=status.HTTP_403_FORBIDDEN)
        membership.status = ser.validated_data['status']
        membership.save(update_fields=['status'])
        return Response({'status': membership.status})
