import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AITask, BotProfile, UserMemory
from .serializers import (
    AITaskSerializer,
    BotProfileSerializer,
    ClassifyRequestSerializer,
    ComposeRequestSerializer,
    EditorActionRequestSerializer,
    ReplyRequestSerializer,
    SummarizeRequestSerializer,
    UserMemorySerializer,
)

logger = logging.getLogger(__name__)


class BotListView(APIView):
    """GET /api/v1/ai/bots — List available AI bots."""
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['AI'], responses=BotProfileSerializer(many=True))
    def get(self, request):
        bots = BotProfile.accessible_by(request.user).select_related('user')
        serializer = BotProfileSerializer(bots, many=True)
        return Response(serializer.data)


class SummarizeView(APIView):
    """POST /api/v1/ai/tasks/mail/summarize — Start email summarization."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['AI'],
        request=SummarizeRequestSerializer,
        responses={202: AITaskSerializer},
    )
    def post(self, request):
        from workspace.ai.client import is_ai_enabled
        if not is_ai_enabled():
            return Response(
                {'detail': 'AI is not configured.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        from workspace.users.settings_service import get_setting
        if get_setting(request.user, 'mail', 'ai_enabled', default=True) is False:
            return Response(
                {'detail': 'Mail AI features are disabled in your settings.'},
                status=status.HTTP_403_FORBIDDEN,
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
    """POST /api/v1/ai/tasks/mail/compose — Start email composition."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['AI'],
        request=ComposeRequestSerializer,
        responses={202: AITaskSerializer},
    )
    def post(self, request):
        from workspace.ai.client import is_ai_enabled
        if not is_ai_enabled():
            return Response(
                {'detail': 'AI is not configured.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        from workspace.users.settings_service import get_setting
        if get_setting(request.user, 'mail', 'ai_enabled', default=True) is False:
            return Response(
                {'detail': 'Mail AI features are disabled in your settings.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = ComposeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        input_data = {
            'instructions': serializer.validated_data['instructions'],
            'context': serializer.validated_data.get('context', ''),
        }
        if serializer.validated_data.get('account_id'):
            input_data['account_id'] = str(serializer.validated_data['account_id'])

        ai_task = AITask.objects.create(
            owner=request.user,
            task_type=AITask.TaskType.COMPOSE,
            input_data=input_data,
        )

        from workspace.ai.tasks import compose_email
        compose_email.delay(str(ai_task.uuid))

        return Response(
            AITaskSerializer(ai_task).data,
            status=status.HTTP_202_ACCEPTED,
        )


class ReplyView(APIView):
    """POST /api/v1/ai/tasks/mail/reply — Generate an email reply."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['AI'],
        request=ReplyRequestSerializer,
        responses={202: AITaskSerializer},
    )
    def post(self, request):
        from workspace.ai.client import is_ai_enabled
        if not is_ai_enabled():
            return Response(
                {'detail': 'AI is not configured.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        from workspace.users.settings_service import get_setting
        if get_setting(request.user, 'mail', 'ai_enabled', default=True) is False:
            return Response(
                {'detail': 'Mail AI features are disabled in your settings.'},
                status=status.HTTP_403_FORBIDDEN,
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


class EditorActionView(APIView):
    """POST /api/v1/ai/tasks/editor — Run an AI action on editor content."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['AI'],
        request=EditorActionRequestSerializer,
        responses={202: AITaskSerializer},
    )
    def post(self, request):
        from workspace.ai.client import is_ai_enabled
        if not is_ai_enabled():
            return Response(
                {'detail': 'AI is not configured.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        serializer = EditorActionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        input_data = {
            'action': data['action'],
            'content': data['content'],
            'language': data.get('language', ''),
            'filename': data.get('filename', ''),
        }
        if data.get('instructions'):
            input_data['instructions'] = data['instructions']

        ai_task = AITask.objects.create(
            owner=request.user,
            task_type=AITask.TaskType.EDITOR,
            input_data=input_data,
        )

        from workspace.ai.tasks import editor_action
        editor_action.delay(str(ai_task.uuid))

        return Response(
            AITaskSerializer(ai_task).data,
            status=status.HTTP_202_ACCEPTED,
        )


class ClassifyView(APIView):
    """POST /api/v1/ai/tasks/mail/classify — Start batch email classification."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['AI'],
        request=ClassifyRequestSerializer,
        responses={202: AITaskSerializer},
    )
    def post(self, request):
        from datetime import timedelta

        from django.utils import timezone

        from workspace.ai.client import is_ai_enabled
        if not is_ai_enabled():
            return Response(
                {'detail': 'AI is not configured.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        from workspace.users.settings_service import get_setting
        if get_setting(request.user, 'mail', 'ai_enabled', default=True) is False:
            return Response(
                {'detail': 'Mail AI features are disabled in your settings.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = ClassifyRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from workspace.mail.models import MailFolder, MailMessage
        from workspace.mail.queries import user_account_ids

        account_ids = user_account_ids(request.user)
        account_id = serializer.validated_data.get('account_id')
        folder_id = serializer.validated_data.get('folder_id')

        # Access control
        if account_id:
            if not account_ids.filter(pk=account_id).exists():
                return Response(status=status.HTTP_404_NOT_FOUND)
            account_ids = account_ids.filter(pk=account_id)

        if folder_id:
            if not MailFolder.objects.filter(uuid=folder_id, account_id__in=account_ids).exists():
                return Response(status=status.HTTP_404_NOT_FOUND)

        # Rate limit: 1 per user per 5 minutes
        cutoff = timezone.now() - timedelta(minutes=5)
        if AITask.objects.filter(
            owner=request.user,
            task_type=AITask.TaskType.CLASSIFY,
            status__in=[AITask.Status.PENDING, AITask.Status.PROCESSING, AITask.Status.COMPLETED],
            created_at__gte=cutoff,
        ).exists():
            return Response(
                {'detail': 'A classification task is already in progress. Try again later.'},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        # Collect unclassified inbox messages (no labels assigned yet)
        qs = MailMessage.objects.filter(
            account_id__in=account_ids,
            deleted_at__isnull=True,
        ).filter(
            message_labels__isnull=True,
        )
        if folder_id:
            qs = qs.filter(folder_id=folder_id)
        else:
            qs = qs.filter(folder__folder_type='inbox')

        message_uuids = list(qs.values_list('uuid', flat=True)[:500])

        ai_task = AITask.objects.create(
            owner=request.user,
            task_type=AITask.TaskType.CLASSIFY,
            input_data={'message_uuids': [str(u) for u in message_uuids]},
        )

        from workspace.ai.tasks import classify_mail_messages
        classify_mail_messages.delay(str(ai_task.uuid))

        return Response(
            AITaskSerializer(ai_task).data,
            status=status.HTTP_202_ACCEPTED,
        )


class TaskDetailView(APIView):
    """GET /api/v1/ai/tasks/<uuid> — Get task status and result."""
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['AI'], responses=AITaskSerializer)
    def get(self, request, task_id):
        try:
            ai_task = AITask.objects.get(pk=task_id, owner=request.user)
        except AITask.DoesNotExist:
            return Response(
                {'detail': 'Task not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(AITaskSerializer(ai_task).data)


class MemoryListView(APIView):
    """GET /api/v1/ai/memories — List current user's memories."""
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['AI'], responses=UserMemorySerializer(many=True))
    def get(self, request):
        memories = UserMemory.objects.filter(user=request.user).select_related('bot')
        bot_id = request.query_params.get('bot_id')
        if bot_id:
            memories = memories.filter(bot_id=bot_id)
        serializer = UserMemorySerializer(memories, many=True)
        return Response(serializer.data)


class MemoryDetailView(APIView):
    """PATCH/DELETE /api/v1/ai/memories/<id>"""
    permission_classes = [IsAuthenticated]

    def _get_memory(self, request, pk):
        try:
            return UserMemory.objects.get(pk=pk, user=request.user)
        except UserMemory.DoesNotExist:
            return None

    @extend_schema(tags=['AI'], request=UserMemorySerializer, responses=UserMemorySerializer)
    def patch(self, request, pk):
        memory = self._get_memory(request, pk)
        if not memory:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = UserMemorySerializer(memory, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @extend_schema(tags=['AI'])
    def delete(self, request, pk):
        memory = self._get_memory(request, pk)
        if not memory:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        memory.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
