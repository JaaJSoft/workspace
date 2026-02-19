from datetime import timedelta

from dateutil.parser import parse as dateutil_parse
from django.contrib.auth import get_user_model
from django.db.models import Q, Prefetch
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


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
    except (ValueError, TypeError):
        return None

from workspace.notifications.services import notify, notify_many
from .models import Calendar, CalendarSubscription, Event, EventMember
from .recurrence import expand_recurring_events, make_virtual_occurrence
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


def _update_event_fields(event, data, user):
    """Apply common field updates to an event."""
    if 'calendar_id' in data:
        try:
            cal = Calendar.objects.get(pk=data['calendar_id'], owner=user)
            event.calendar = cal
        except Calendar.DoesNotExist:
            return {'detail': 'Calendar not found.'}

    for field in ['title', 'description', 'start', 'end', 'all_day', 'location',
                  'recurrence_frequency', 'recurrence_interval', 'recurrence_end']:
        if field in data:
            setattr(event, field, data[field])
    event.save()
    return None


def _sync_members(event, member_ids, owner_id):
    """Sync event members from a list of user IDs."""
    current = set(event.members.values_list('user_id', flat=True))
    new_ids = set(member_ids) - {owner_id}
    to_remove = current - new_ids
    if to_remove:
        removed_users = list(User.objects.filter(id__in=to_remove))
        EventMember.objects.filter(event=event, user_id__in=to_remove).delete()
        if removed_users:
            notify_many(
                recipients=removed_users,
                origin='calendar',
                title=f'Removed from "{event.title}"',
                body=f'{event.owner.username} removed you from an event.',
                url=f'/calendar?event={event.pk}',
                actor=event.owner,
            )
    to_add = new_ids - current
    if to_add:
        users = list(User.objects.filter(id__in=to_add))
        EventMember.objects.bulk_create([EventMember(event=event, user=u) for u in users])
        notify_many(
            recipients=users,
            origin='calendar',
            title=f'Invited to "{event.title}"',
            body=f'{event.owner.username} invited you to an event.',
            url=f'/calendar?event={event.pk}',
            actor=event.owner,
        )


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

        range_start = _parse_dt(start)
        range_end = _parse_dt(end)

        user = request.user

        # Filter by specific calendars or all visible
        calendar_ids_param = request.query_params.get('calendar_ids')
        if calendar_ids_param is not None:
            cal_ids = [c.strip() for c in calendar_ids_param.split(',') if c.strip()]
        else:
            cal_ids = _visible_calendar_ids(user)

        cal_or_member = Q(calendar_id__in=cal_ids) | Q(members__user=user)

        # Non-recurring events (exclude exceptions)
        non_recurring = Event.objects.filter(
            cal_or_member,
            recurrence_frequency__isnull=True,
            recurrence_parent__isnull=True,
            is_cancelled=False,
            start__lt=end,
        ).filter(
            Q(end__gt=start) | Q(end__isnull=True, start__gte=start),
        ).distinct()
        non_recurring = _prefetch_event(non_recurring).order_by('start')

        non_recurring_data = EventSerializer(non_recurring, many=True).data

        # Recurring masters overlapping the range
        masters = Event.objects.filter(
            cal_or_member,
            recurrence_frequency__isnull=False,
            recurrence_parent__isnull=True,
            start__lt=end,
        ).filter(
            Q(recurrence_end__isnull=True) | Q(recurrence_end__gt=start),
        ).distinct()
        masters = _prefetch_event(masters)

        recurring_data = []
        if range_start and range_end:
            recurring_data = expand_recurring_events(masters, range_start, range_end)

        # Merge and sort
        all_events = non_recurring_data + recurring_data
        all_events.sort(key=lambda e: e.get('start', ''))
        return Response(all_events)

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
            recurrence_frequency=data.get('recurrence_frequency'),
            recurrence_interval=data.get('recurrence_interval', 1),
            recurrence_end=data.get('recurrence_end'),
        )

        member_ids = data.get('member_ids', [])
        if member_ids:
            users = list(User.objects.filter(id__in=member_ids).exclude(id=request.user.id))
            EventMember.objects.bulk_create([
                EventMember(event=event, user=u) for u in users
            ])
            notify_many(
                recipients=users,
                origin='calendar',
                title=f'Invited to "{event.title}"',
                body=f'{request.user.username} invited you to an event.',
                url=f'/calendar?event={event.pk}',
                actor=request.user,
            )

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

        # If original_start is provided, return the specific occurrence
        original_start_str = request.query_params.get('original_start')
        if original_start_str and event.is_recurring:
            original_start = _parse_dt(original_start_str)
            if original_start:
                # Check for a materialized exception first
                exc = Event.objects.filter(
                    recurrence_parent=event,
                    original_start=original_start,
                ).first()
                if exc:
                    exc = _prefetch_event(Event.objects.filter(pk=exc.pk)).first()
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
            return Response({'detail': 'Only the owner can edit.'}, status=status.HTTP_403_FORBIDDEN)

        ser = EventUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        scope = data.pop('scope', 'all')
        original_start_val = data.pop('original_start', None)

        # Non-recurring event or scope='all': update the event directly
        if not event.is_recurring or scope == 'all':
            err = _update_event_fields(event, data, request.user)
            if err:
                return Response(err, status=status.HTTP_400_BAD_REQUEST)

            if 'member_ids' in data:
                _sync_members(event, data['member_ids'], request.user.id)

            # Notify existing members about the update
            member_users = list(User.objects.filter(
                calendar_invitations__event=event,
            ).exclude(id=request.user.id))
            if member_users:
                notify_many(
                    recipients=member_users,
                    origin='calendar',
                    title=f'"{event.title}" was updated',
                    body=f'{request.user.username} updated an event you are part of.',
                    url=f'/calendar?event={event.pk}',
                    actor=request.user,
                )

            event = _prefetch_event(Event.objects.filter(pk=event.pk)).first()
            return Response(EventSerializer(event).data)

        if scope == 'this':
            return self._edit_single_occurrence(event, data, original_start_val, request.user)

        if scope == 'future':
            return self._edit_future_occurrences(event, data, original_start_val, request.user)

        return Response({'detail': 'Invalid scope.'}, status=status.HTTP_400_BAD_REQUEST)

    def _edit_single_occurrence(self, master, data, original_start, user):
        """Create a materialized exception for a single occurrence."""
        if not original_start:
            return Response({'detail': 'original_start is required for scope=this.'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Check if exception already exists
        exc = Event.objects.filter(
            recurrence_parent=master, original_start=original_start,
        ).first()

        if exc:
            # Update existing exception
            err = _update_event_fields(exc, data, user)
            if err:
                return Response(err, status=status.HTTP_400_BAD_REQUEST)
            if 'member_ids' in data:
                _sync_members(exc, data['member_ids'], user.id)
            exc = _prefetch_event(Event.objects.filter(pk=exc.pk)).first()
            return Response(EventSerializer(exc).data)

        # Create new exception: inherit fields from master, apply overrides
        duration = (master.end - master.start) if master.end else None
        exc = Event.objects.create(
            calendar=master.calendar,
            title=data.get('title', master.title),
            description=data.get('description', master.description),
            start=data.get('start', original_start),
            end=data.get('end', (original_start + duration) if duration else None),
            all_day=data.get('all_day', master.all_day),
            location=data.get('location', master.location),
            owner=master.owner,
            recurrence_parent=master,
            original_start=original_start,
        )

        # Copy members from master or from data
        if 'member_ids' in data:
            member_ids = set(data['member_ids']) - {user.id}
            existing_ids = set(master.members.values_list('user_id', flat=True))
            users = list(User.objects.filter(id__in=member_ids))
            EventMember.objects.bulk_create([EventMember(event=exc, user=u) for u in users])
            new_users = [u for u in users if u.id not in existing_ids]
            if new_users:
                notify_many(
                    recipients=new_users,
                    origin='calendar',
                    title=f'Invited to "{exc.title}"',
                    body=f'{user.username} invited you to an event.',
                    url=f'/calendar?event={exc.pk}',
                    actor=user,
                )
        else:
            # Copy from master
            EventMember.objects.bulk_create([
                EventMember(event=exc, user=m.user, status=m.status)
                for m in master.members.all()
            ])

        exc = _prefetch_event(Event.objects.filter(pk=exc.pk)).first()
        return Response(EventSerializer(exc).data)

    def _edit_future_occurrences(self, master, data, original_start, user):
        """Split the series: truncate old master, create new master from original_start."""
        if not original_start:
            return Response({'detail': 'original_start is required for scope=future.'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Truncate old master
        master.recurrence_end = original_start - timedelta(seconds=1)
        master.save(update_fields=['recurrence_end'])

        # Delete exceptions >= original_start
        Event.objects.filter(
            recurrence_parent=master,
            original_start__gte=original_start,
        ).delete()

        # Create new master
        duration = (master.end - master.start) if master.end else None
        new_master = Event.objects.create(
            calendar=data.get('calendar_id', master.calendar_id) and master.calendar,
            title=data.get('title', master.title),
            description=data.get('description', master.description),
            start=data.get('start', original_start),
            end=data.get('end', (original_start + duration) if duration else None),
            all_day=data.get('all_day', master.all_day),
            location=data.get('location', master.location),
            owner=master.owner,
            recurrence_frequency=data.get('recurrence_frequency', master.recurrence_frequency),
            recurrence_interval=data.get('recurrence_interval', master.recurrence_interval),
            recurrence_end=data.get('recurrence_end', master.recurrence_end),
        )

        # Handle calendar_id change
        if 'calendar_id' in data:
            try:
                cal = Calendar.objects.get(pk=data['calendar_id'], owner=user)
                new_master.calendar = cal
                new_master.save(update_fields=['calendar_id'])
            except Calendar.DoesNotExist:
                pass

        # Copy members
        if 'member_ids' in data:
            member_ids = set(data['member_ids']) - {user.id}
            existing_ids = set(master.members.values_list('user_id', flat=True))
            users = list(User.objects.filter(id__in=member_ids))
            EventMember.objects.bulk_create([EventMember(event=new_master, user=u) for u in users])
            new_users = [u for u in users if u.id not in existing_ids]
            if new_users:
                notify_many(
                    recipients=new_users,
                    origin='calendar',
                    title=f'Invited to "{new_master.title}"',
                    body=f'{user.username} invited you to an event.',
                    url=f'/calendar?event={new_master.pk}',
                    actor=user,
                )
        else:
            EventMember.objects.bulk_create([
                EventMember(event=new_master, user=m.user, status=m.status)
                for m in master.members.all()
            ])

        new_master = _prefetch_event(Event.objects.filter(pk=new_master.pk)).first()
        return Response(EventSerializer(new_master).data)

    @extend_schema(summary="Delete an event")
    def delete(self, request, event_id):
        event, err = self._get_event(event_id, request.user)
        if err:
            return err
        if event.owner_id != request.user.id:
            return Response({'detail': 'Only the owner can delete.'}, status=status.HTTP_403_FORBIDDEN)

        scope = request.query_params.get('scope', 'all')
        original_start_str = request.query_params.get('original_start')

        if not event.is_recurring or scope == 'all':
            member_users = list(User.objects.filter(
                calendar_invitations__event=event,
            ).exclude(id=request.user.id))
            if member_users:
                notify_many(
                    recipients=member_users,
                    origin='calendar',
                    title=f'"{event.title}" was cancelled',
                    body=f'{request.user.username} cancelled an event you were part of.',
                    actor=request.user,
                )
            event.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        if scope == 'this':
            if not original_start_str:
                return Response({'detail': 'original_start is required for scope=this.'},
                                status=status.HTTP_400_BAD_REQUEST)
            original_start = _parse_dt(original_start_str)

            # Check if there's already an exception
            exc = Event.objects.filter(
                recurrence_parent=event, original_start=original_start,
            ).first()
            if exc:
                exc.is_cancelled = True
                exc.save(update_fields=['is_cancelled'])
            else:
                # Create a cancelled exception
                Event.objects.create(
                    calendar=event.calendar,
                    title=event.title,
                    start=original_start,
                    owner=event.owner,
                    recurrence_parent=event,
                    original_start=original_start,
                    is_cancelled=True,
                )
            return Response(status=status.HTTP_204_NO_CONTENT)

        if scope == 'future':
            if not original_start_str:
                return Response({'detail': 'original_start is required for scope=future.'},
                                status=status.HTTP_400_BAD_REQUEST)
            original_start = _parse_dt(original_start_str)

            event.recurrence_end = original_start - timedelta(seconds=1)
            event.save(update_fields=['recurrence_end'])

            # Delete exceptions >= original_start
            Event.objects.filter(
                recurrence_parent=event,
                original_start__gte=original_start,
            ).delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        return Response({'detail': 'Invalid scope.'}, status=status.HTTP_400_BAD_REQUEST)


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
        event = Event.objects.select_related('owner').get(pk=event_id)
        if event.owner_id != request.user.id:
            status_label = membership.status  # 'accepted' or 'declined'
            notify(
                recipient=event.owner,
                origin='calendar',
                title=f'{request.user.username} {status_label} "{event.title}"',
                url=f'/calendar?event={event.pk}',
                actor=request.user,
            )
        return Response({'status': membership.status})
