from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from workspace.ai.models import AITask, BotProfile
from workspace.chat.models import Conversation, ConversationMember, Message

User = get_user_model()


@override_settings(
    AI_API_KEY='test-key',
    AI_MODEL='gpt-4o-mini',
    AI_MAX_TOKENS=100,
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
class GenerateChatResponseTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='user', password='pass123')
        self.bot_user = User.objects.create_user(username='bot', password='pass123')
        self.bot_profile = BotProfile.objects.create(
            user=self.bot_user,
            system_prompt='You are a test bot.',
        )
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conversation, user=self.user)
        ConversationMember.objects.create(conversation=self.conversation, user=self.bot_user)
        self.message = Message.objects.create(
            conversation=self.conversation,
            author=self.user,
            body='Hello bot!',
        )

    @patch('workspace.ai.client.get_ai_client')
    def test_generates_response(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content='Hello human!'))]
        mock_response.model = 'gpt-4o-mini'
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        from workspace.ai.tasks import generate_chat_response
        result = generate_chat_response(
            str(self.conversation.uuid),
            str(self.message.uuid),
            self.bot_user.id,
        )

        self.assertEqual(result['status'], 'ok')
        bot_msg = Message.objects.filter(author=self.bot_user).first()
        self.assertIsNotNone(bot_msg)
        self.assertEqual(bot_msg.body, 'Hello human!')

    @patch('workspace.ai.client.get_ai_client')
    def test_handles_api_error(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception('API Error')
        mock_get_client.return_value = mock_client

        from workspace.ai.tasks import generate_chat_response
        result = generate_chat_response(
            str(self.conversation.uuid),
            str(self.message.uuid),
            self.bot_user.id,
        )

        self.assertEqual(result['status'], 'error')
        task = AITask.objects.filter(task_type='chat').first()
        self.assertEqual(task.status, AITask.Status.FAILED)
