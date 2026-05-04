import logging

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Message
from .services.conversations import get_active_membership

logger = logging.getLogger(__name__)


@extend_schema(tags=['Chat'])
class BotRetryView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Retry a failed bot response")
    def post(self, request, conversation_id, message_id):
        # Lazy import to avoid circular dependency with views.py
        from .views import _trigger_bot_response

        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(status=status.HTTP_404_NOT_FOUND)

        # Validate the target message exists, belongs to this conversation, and
        # is a bot message before deleting. Otherwise any active member could
        # pass an arbitrary message_id and hard-delete it.
        target = (
            Message.objects.select_related('author__bot_profile')
            .filter(uuid=message_id, conversation_id=conversation_id)
            .first()
        )
        if not target:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if not hasattr(target.author, 'bot_profile'):
            return Response(
                {'detail': 'Only bot messages can be retried.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Find the last user message to retry with — required before delete.
        last_user_msg = (
            Message.objects.filter(
                conversation_id=conversation_id,
                author=request.user,
                deleted_at__isnull=True,
            )
            .order_by('-created_at')
            .first()
        )
        if not last_user_msg:
            return Response(
                {'detail': 'No user message found to retry.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        target.delete()

        _trigger_bot_response(conversation_id, last_user_msg, request.user)
        return Response({'status': 'ok'})


@extend_schema(tags=['Chat'])
class BotCancelView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Cancel an in-progress bot response")
    def post(self, request, conversation_id):
        from workspace.ai.models import AITask

        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(status=status.HTTP_404_NOT_FOUND)

        cancelled = AITask.objects.filter(
            task_type=AITask.TaskType.CHAT,
            status__in=[AITask.Status.PENDING, AITask.Status.PROCESSING],
            input_data__conversation_id=str(conversation_id),
        ).update(
            status=AITask.Status.FAILED,
            error='Cancelled by user',
            completed_at=timezone.now(),
        )

        if not cancelled:
            return Response({'detail': 'No active task found.'}, status=status.HTTP_404_NOT_FOUND)

        return Response({'status': 'cancelled'})
