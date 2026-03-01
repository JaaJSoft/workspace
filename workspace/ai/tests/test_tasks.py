from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from workspace.ai.models import AITask, BotProfile
from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.mail.models import MailAccount, MailFolder, MailMessage

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


@override_settings(
    AI_API_KEY='test-key',
    AI_MODEL='gpt-4o-mini',
    AI_MAX_TOKENS=100,
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
class SummarizeTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='user', password='pass123')
        self.account = MailAccount.objects.create(
            owner=self.user,
            email='user@example.com',
            imap_host='imap.example.com',
            smtp_host='smtp.example.com',
        )
        self.folder = MailFolder.objects.create(
            account=self.account,
            name='INBOX',
            folder_type='inbox',
        )
        self.message = MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            imap_uid=1,
            subject='Test email',
            body_text='Hello, this is a test email with important content.',
        )

    @patch('workspace.ai.client.get_ai_client')
    def test_summary_persisted_on_message(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content='• Key point from email'))]
        mock_response.model = 'gpt-4o-mini'
        mock_response.usage = MagicMock(prompt_tokens=50, completion_tokens=10)
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.SUMMARIZE,
            input_data={'message_id': str(self.message.uuid)},
        )

        from workspace.ai.tasks import summarize
        result = summarize(str(task.uuid))

        self.assertEqual(result['status'], 'ok')
        self.message.refresh_from_db()
        self.assertEqual(self.message.ai_summary, '• Key point from email')

    @patch('workspace.ai.client.get_ai_client')
    def test_re_summarize_overwrites(self, mock_get_client):
        self.message.ai_summary = 'Old summary'
        self.message.save(update_fields=['ai_summary'])

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content='• New summary'))]
        mock_response.model = 'gpt-4o-mini'
        mock_response.usage = MagicMock(prompt_tokens=50, completion_tokens=10)
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.SUMMARIZE,
            input_data={'message_id': str(self.message.uuid)},
        )

        from workspace.ai.tasks import summarize
        summarize(str(task.uuid))

        self.message.refresh_from_db()
        self.assertEqual(self.message.ai_summary, '• New summary')
