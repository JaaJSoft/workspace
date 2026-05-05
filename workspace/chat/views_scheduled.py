import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import ScheduledMessageSerializer
from .services.conversations import get_active_membership

logger = logging.getLogger(__name__)


@extend_schema(tags=['Chat'])
class ScheduledMessageListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="List active scheduled messages for a conversation")
    def get(self, request, conversation_id):
        from workspace.ai.models import ScheduledMessage

        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        schedules = (
            ScheduledMessage.objects.filter(
                conversation_id=conversation_id,
                is_active=True,
            )
            .select_related('bot')
            .order_by('next_run_at')
        )
        serializer = ScheduledMessageSerializer(schedules, many=True)
        return Response(serializer.data)


@extend_schema(tags=['Chat'])
class ScheduledMessageDetailView(APIView):
    permission_classes = [IsAuthenticated]

    TIMING_FIELDS = {
        'scheduled_at', 'recurrence_unit', 'recurrence_interval',
        'recurrence_time', 'recurrence_day', 'kind',
    }

    @extend_schema(summary="Update a scheduled message")
    def patch(self, request, conversation_id, schedule_id):
        from workspace.ai.models import ScheduledMessage

        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        schedule = (
            ScheduledMessage.objects.filter(
                uuid=schedule_id,
                conversation_id=conversation_id,
                is_active=True,
            )
            .select_related('bot')
            .first()
        )
        if not schedule:
            return Response(
                {'detail': 'Scheduled message not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = ScheduledMessageSerializer(schedule, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = serializer.save()

        # Recompute next_run_at if any timing fields were changed.
        # ONCE schedules can't go through compute_next_run(): it deactivates
        # them on the assumption it's being called post-fire by the dispatcher.
        if self.TIMING_FIELDS & set(request.data.keys()):
            if updated.kind == ScheduledMessage.Kind.ONCE:
                updated.next_run_at = updated.scheduled_at
                updated.save(update_fields=['next_run_at'])
            else:
                from workspace.users.services.settings import get_user_timezone
                updated.compute_next_run(user_tz=get_user_timezone(request.user))
                updated.save(update_fields=['next_run_at', 'is_active'])

        return Response(ScheduledMessageSerializer(updated).data)

    @extend_schema(summary="Deactivate a scheduled message")
    def delete(self, request, conversation_id, schedule_id):
        from workspace.ai.models import ScheduledMessage

        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        schedule = ScheduledMessage.objects.filter(
            uuid=schedule_id,
            conversation_id=conversation_id,
            is_active=True,
        ).first()
        if not schedule:
            return Response(
                {'detail': 'Scheduled message not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        schedule.is_active = False
        schedule.save(update_fields=['is_active'])
        return Response(status=status.HTTP_204_NO_CONTENT)
