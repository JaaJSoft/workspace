import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AITask, BotProfile
from .serializers import (
    AITaskSerializer,
    BotProfileSerializer,
    ComposeRequestSerializer,
    ReplyRequestSerializer,
    SummarizeRequestSerializer,
)

logger = logging.getLogger(__name__)


class BotListView(APIView):
    """GET /api/v1/ai/bots — List available AI bots."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        bots = BotProfile.objects.select_related('user').all()
        serializer = BotProfileSerializer(bots, many=True)
        return Response(serializer.data)


class SummarizeView(APIView):
    """POST /api/v1/ai/tasks/summarize — Start email summarization."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from workspace.ai.client import is_ai_enabled
        if not is_ai_enabled():
            return Response(
                {'detail': 'AI is not configured.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        serializer = SummarizeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from workspace.mail.models import MailMessage
        message_id = serializer.validated_data['message_id']
        if not MailMessage.objects.filter(pk=message_id, account__owner=request.user).exists():
            return Response(
                {'detail': 'Mail message not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        ai_task = AITask.objects.create(
            owner=request.user,
            task_type=AITask.TaskType.SUMMARIZE,
            input_data={'message_id': str(message_id)},
        )

        from workspace.ai.tasks import summarize
        summarize.delay(str(ai_task.uuid))

        return Response(
            AITaskSerializer(ai_task).data,
            status=status.HTTP_202_ACCEPTED,
        )


class ComposeView(APIView):
    """POST /api/v1/ai/tasks/compose — Start email composition."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from workspace.ai.client import is_ai_enabled
        if not is_ai_enabled():
            return Response(
                {'detail': 'AI is not configured.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        serializer = ComposeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        ai_task = AITask.objects.create(
            owner=request.user,
            task_type=AITask.TaskType.COMPOSE,
            input_data={
                'instructions': serializer.validated_data['instructions'],
                'context': serializer.validated_data.get('context', ''),
            },
        )

        from workspace.ai.tasks import compose_email
        compose_email.delay(str(ai_task.uuid))

        return Response(
            AITaskSerializer(ai_task).data,
            status=status.HTTP_202_ACCEPTED,
        )


class ReplyView(APIView):
    """POST /api/v1/ai/tasks/reply — Generate an email reply."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from workspace.ai.client import is_ai_enabled
        if not is_ai_enabled():
            return Response(
                {'detail': 'AI is not configured.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        serializer = ReplyRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from workspace.mail.models import MailMessage
        message_id = serializer.validated_data['message_id']
        if not MailMessage.objects.filter(pk=message_id, account__owner=request.user).exists():
            return Response(
                {'detail': 'Mail message not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        ai_task = AITask.objects.create(
            owner=request.user,
            task_type=AITask.TaskType.REPLY,
            input_data={
                'message_id': str(message_id),
                'instructions': serializer.validated_data['instructions'],
            },
        )

        from workspace.ai.tasks import compose_email
        compose_email.delay(str(ai_task.uuid))

        return Response(
            AITaskSerializer(ai_task).data,
            status=status.HTTP_202_ACCEPTED,
        )


class TaskDetailView(APIView):
    """GET /api/v1/ai/tasks/<uuid> — Get task status and result."""
    permission_classes = [IsAuthenticated]

    def get(self, request, task_id):
        try:
            ai_task = AITask.objects.get(pk=task_id, owner=request.user)
        except AITask.DoesNotExist:
            return Response(
                {'detail': 'Task not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(AITaskSerializer(ai_task).data)
