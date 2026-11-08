import logging

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ..serializers import ScheduledMessageSerializer
from ..services.bot_tasks import can_manage_bot_task
from ..services.conversations import get_active_membership

logger = logging.getLogger(__name__)


@extend_schema(tags=["Chat - Conversations"])
class ScheduledMessageListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="List active scheduled messages for a conversation")
    def get(self, request, conversation_id):
        from workspace.ai.models import ScheduledMessage

        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {"detail": "Not a member of this conversation."},
                status=status.HTTP_403_FORBIDDEN,
            )

        schedules = (
            ScheduledMessage.objects.filter(
                conversation_id=conversation_id,
                is_active=True,
            )
            .select_related("bot")
            .order_by("next_run_at")
        )
        serializer = ScheduledMessageSerializer(
            schedules, many=True, context={"request": request}
        )
        return Response(serializer.data)


@extend_schema(tags=["Chat - Conversations"])
class ScheduledMessageDetailView(APIView):
    permission_classes = [IsAuthenticated]

    TIMING_FIELDS = {
        "scheduled_at",
        "recurrence_unit",
        "recurrence_interval",
        "recurrence_time",
        "recurrence_day",
        "kind",
    }

    @extend_schema(summary="Update a scheduled message")
    def patch(self, request, conversation_id, schedule_id):
        from workspace.ai.models import ScheduledMessage

        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {"detail": "Not a member of this conversation."},
                status=status.HTTP_403_FORBIDDEN,
            )

        schedule = (
            ScheduledMessage.objects.filter(
                uuid=schedule_id,
                conversation_id=conversation_id,
                is_active=True,
            )
            .select_related("bot")
            .first()
        )
        if not schedule:
            return Response(
                {"detail": "Scheduled message not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not can_manage_bot_task(request.user, schedule):
            return Response(
                {"detail": "Only the person who scheduled this message can change it."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = ScheduledMessageSerializer(
            schedule, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        changes = dict(serializer.validated_data)
        for field, value in changes.items():
            setattr(schedule, field, value)

        # Recompute next_run_at if any timing fields were changed.
        # ONCE schedules can't go through compute_next_run(): it deactivates
        # them on the assumption it's being called post-fire by the dispatcher.
        if self.TIMING_FIELDS & changes.keys():
            if schedule.kind == ScheduledMessage.Kind.ONCE:
                schedule.next_run_at = schedule.scheduled_at
            else:
                from workspace.users.services.settings import get_user_timezone

                schedule.compute_next_run(user_tz=get_user_timezone(request.user))
            changes["next_run_at"] = schedule.next_run_at

        # Only the columns this request changed, and only while the schedule
        # is still active: the dispatcher writes next_run_at and is_active
        # while the request runs, and a one-time message that fired meanwhile
        # must not come back to fire twice.
        updated = ScheduledMessage.objects.filter(
            pk=schedule.pk, is_active=True
        ).update(**changes, updated_at=timezone.now())
        if not updated:
            return Response(
                {"detail": "Scheduled message not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        schedule.refresh_from_db()

        return Response(
            ScheduledMessageSerializer(schedule, context={"request": request}).data
        )

    @extend_schema(summary="Deactivate a scheduled message")
    def delete(self, request, conversation_id, schedule_id):
        from workspace.ai.models import ScheduledMessage

        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {"detail": "Not a member of this conversation."},
                status=status.HTTP_403_FORBIDDEN,
            )

        schedule = ScheduledMessage.objects.filter(
            uuid=schedule_id,
            conversation_id=conversation_id,
            is_active=True,
        ).first()
        if not schedule:
            return Response(
                {"detail": "Scheduled message not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not can_manage_bot_task(request.user, schedule):
            return Response(
                {"detail": "Only the person who scheduled this message can change it."},
                status=status.HTTP_403_FORBIDDEN,
            )

        schedule.is_active = False
        schedule.save(update_fields=["is_active"])
        return Response(status=status.HTTP_204_NO_CONTENT)
