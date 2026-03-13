import base64
import json
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from workspace.ai.models import AITask, BotProfile
from workspace.ai.tools import CoreToolProvider
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
        mock_response.choices = [MagicMock(message=MagicMock(content='Hello human!', tool_calls=None))]
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


@override_settings(
    AI_API_KEY='test-key',
    AI_MODEL='gpt-4o-mini',
    AI_MAX_TOKENS=100,
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
class GenerateChatResponseWithToolsTests(TestCase):
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
            title='Test conversation',
        )
        ConversationMember.objects.create(conversation=self.conversation, user=self.user)
        ConversationMember.objects.create(conversation=self.conversation, user=self.bot_user)
        self.message = Message.objects.create(
            conversation=self.conversation,
            author=self.user,
            body='My name is Pierre',
        )

    @patch('workspace.ai.client.get_ai_client')
    def test_tool_call_saves_memory_then_responds(self, mock_get_client):
        mock_client = MagicMock()

        # First call: tool_calls response
        tool_call = MagicMock()
        tool_call.id = 'call_abc'
        tool_call.type = 'function'
        tool_call.function.name = 'save_memory'
        tool_call.function.arguments = '{"key": "name", "content": "Pierre"}'

        first_response = MagicMock()
        first_message = MagicMock()
        first_message.content = None
        first_message.tool_calls = [tool_call]
        first_message.role = 'assistant'
        first_message.to_dict.return_value = {
            'role': 'assistant',
            'content': None,
            'tool_calls': [{'id': 'call_abc', 'type': 'function', 'function': {'name': 'save_memory', 'arguments': '{"key": "name", "content": "Pierre"}'}}],
        }
        first_response.choices = [MagicMock(message=first_message, finish_reason='tool_calls')]
        first_response.model = 'gpt-4o-mini'
        first_response.usage = MagicMock(prompt_tokens=20, completion_tokens=10)

        # Second call: final text response
        second_response = MagicMock()
        second_message = MagicMock()
        second_message.content = "Got it, Pierre!"
        second_message.tool_calls = None
        second_response.choices = [MagicMock(message=second_message, finish_reason='stop')]
        second_response.model = 'gpt-4o-mini'
        second_response.usage = MagicMock(prompt_tokens=30, completion_tokens=8)

        mock_client.chat.completions.create.side_effect = [first_response, second_response]
        mock_get_client.return_value = mock_client

        from workspace.ai.tasks import generate_chat_response
        result = generate_chat_response(
            str(self.conversation.uuid),
            str(self.message.uuid),
            self.bot_user.id,
        )

        self.assertEqual(result['status'], 'ok')

        # Memory was saved
        from workspace.ai.models import UserMemory
        mem = UserMemory.objects.get(user=self.user, bot=self.bot_user, key='name')
        self.assertEqual(mem.content, 'Pierre')

        # Final response posted
        bot_msg = Message.objects.filter(author=self.bot_user).first()
        self.assertEqual(bot_msg.body, "Got it, Pierre!")

        # Retention badge appears in body_html
        self.assertIn('Retained:', bot_msg.body_html)
        self.assertIn('name', bot_msg.body_html)

        # Two API calls were made
        self.assertEqual(mock_client.chat.completions.create.call_count, 2)


class GenerateImageToolTest(TestCase):
    """Unit tests for the generate_image tool."""

    def setUp(self):
        from workspace.ai.tools import ImageToolProvider
        self.provider = ImageToolProvider()
        self.conv_id = 'test-conv-img'
        self.context = {}

    @patch('workspace.ai.tools.get_image_client')
    def test_generate_image_success(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.data = [MagicMock(b64_json=base64.b64encode(b'\x89PNG fake').decode())]
        mock_client.images.generate.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = self.provider.generate_image(
            {'prompt': 'a cat'}, user=None, bot=None,
            conversation_id=self.conv_id, context=self.context,
        )

        self.assertIn('successfully', result)
        self.assertEqual(len(self.context['images']), 1)
        self.assertEqual(self.context['images'][0]['prompt'], 'a cat')
        mock_client.images.generate.assert_called_once()

    def test_generate_image_empty_prompt(self):
        result = self.provider.generate_image(
            {'prompt': ''}, user=None, bot=None,
            conversation_id=self.conv_id, context=self.context,
        )
        self.assertIn('Error', result)
        self.assertNotIn('images', self.context)

    def test_generate_image_no_conversation(self):
        result = self.provider.generate_image(
            {'prompt': 'a cat'}, user=None, bot=None,
            conversation_id=None, context=self.context,
        )
        self.assertIn('Error', result)

    @patch('workspace.ai.tools.get_image_client')
    def test_generate_image_invalid_size_defaults(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.data = [MagicMock(b64_json=base64.b64encode(b'\x89PNG fake').decode())]
        mock_client.images.generate.return_value = mock_response
        mock_get_client.return_value = mock_client

        self.provider.generate_image(
            {'prompt': 'a cat', 'size': '999x999'},
            user=None, bot=None, conversation_id=self.conv_id,
            context=self.context,
        )
        call_kwargs = mock_client.images.generate.call_args[1]
        self.assertEqual(call_kwargs['size'], '1024x1024')

    @patch('workspace.ai.tools.get_image_client')
    def test_generate_image_api_error(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.images.generate.side_effect = Exception('API timeout')
        mock_get_client.return_value = mock_client

        result = self.provider.generate_image(
            {'prompt': 'a cat'}, user=None, bot=None,
            conversation_id=self.conv_id, context=self.context,
        )
        self.assertIn('Error', result)
        self.assertNotIn('images', self.context)


class EditImageToolTest(TestCase):
    """Unit tests for the edit_image tool."""

    def setUp(self):
        from workspace.ai.tools import ImageToolProvider
        self.provider = ImageToolProvider()
        self.context = {}
        self.user = User.objects.create_user(username='editimguser', password='pw')
        self.conv = Conversation.objects.create(kind='dm', created_by=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.user)

    def _attach_image(self):
        """Create a message with an image attachment in the conversation."""
        from django.core.files.base import ContentFile
        from workspace.chat.models import MessageAttachment
        msg = Message.objects.create(conversation=self.conv, author=self.user, body='here')
        att = MessageAttachment(
            message=msg, original_name='photo.png', mime_type='image/png', size=8,
        )
        att.file.save('photo.png', ContentFile(b'\x89PNGdata'), save=False)
        att.save()
        return att

    @patch('workspace.ai.image_service.get_image_client')
    def test_edit_image_openai_success(self, mock_get_client):
        """OpenAI images.edit works on first try."""
        self._attach_image()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.data = [MagicMock(b64_json=base64.b64encode(b'\x89PNG edited').decode())]
        mock_client.images.edit.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = self.provider.edit_image(
            {'prompt': 'make it blue'}, user=self.user, bot=None,
            conversation_id=str(self.conv.uuid), context=self.context,
        )

        self.assertIn('successfully', result)
        self.assertEqual(len(self.context['images']), 1)
        self.assertEqual(self.context['images'][0]['prompt'], 'make it blue')
        mock_client.images.edit.assert_called_once()

    @override_settings(
        AI_IMAGE_MODEL='test-model',
        AI_IMAGE_BASE_URL='http://localhost:11434/v1/',
        AI_TIMEOUT=30,
    )
    @patch('workspace.ai.image_service.get_image_client')
    def test_edit_image_ollama_fallback(self, mock_get_client):
        """Falls back to Ollama native API when OpenAI endpoint fails."""
        self._attach_image()
        mock_client = MagicMock()
        mock_client.images.edit.side_effect = Exception('400 Bad Request')
        mock_get_client.return_value = mock_client

        with patch('workspace.ai.image_service._edit_via_ollama',
                   return_value=b'\x89PNG ollama') as mock_ollama:
            result = self.provider.edit_image(
                {'prompt': 'make it red'}, user=self.user, bot=None,
                conversation_id=str(self.conv.uuid), context=self.context,
            )

        self.assertIn('successfully', result)
        self.assertEqual(len(self.context['images']), 1)
        mock_ollama.assert_called_once()

    def test_edit_image_empty_prompt(self):
        result = self.provider.edit_image(
            {'prompt': ''}, user=None, bot=None,
            conversation_id=str(self.conv.uuid), context=self.context,
        )
        self.assertIn('Error', result)

    def test_edit_image_no_image_in_conversation(self):
        result = self.provider.edit_image(
            {'prompt': 'make it blue'}, user=self.user, bot=None,
            conversation_id=str(self.conv.uuid), context=self.context,
        )
        self.assertIn('no image found', result)

    @patch('workspace.ai.image_service.get_image_client')
    def test_edit_image_both_backends_fail(self, mock_get_client):
        """Returns error when both OpenAI and Ollama fail."""
        self._attach_image()
        mock_client = MagicMock()
        mock_client.images.edit.side_effect = Exception('OpenAI failed')
        mock_get_client.return_value = mock_client

        with patch('workspace.ai.image_service._edit_via_ollama',
                   side_effect=Exception('Ollama failed')):
            result = self.provider.edit_image(
                {'prompt': 'make it blue'}, user=self.user, bot=None,
                conversation_id=str(self.conv.uuid), context=self.context,
            )

        self.assertIn('Error', result)
        self.assertNotIn('images', self.context)

    def test_edit_image_no_conversation(self):
        result = self.provider.edit_image(
            {'prompt': 'make it blue'}, user=None, bot=None,
            conversation_id=None, context=self.context,
        )
        self.assertIn('Error', result)


@override_settings(
    AI_API_KEY='test-key',
    AI_MODEL='gpt-4o-mini',
    AI_SMALL_MODEL='gpt-4o-mini',
    AI_MAX_TOKENS=100,
    CELERY_TASK_ALWAYS_EAGER=False,
)
class ClassifyMailMessagesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='clsuser', password='pass123')
        self.account = MailAccount.objects.create(
            owner=self.user, email='cls@test.com',
            imap_host='imap.test.com', smtp_host='smtp.test.com',
            username='cls@test.com',
        )
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX',
            display_name='Inbox', folder_type='inbox',
        )
        self.msg1 = MailMessage.objects.create(
            account=self.account, folder=self.folder, imap_uid=1,
            subject='Server down!', snippet='Production is down',
            from_address={'name': 'Alert', 'email': 'alert@ops.com'},
        )
        self.msg2 = MailMessage.objects.create(
            account=self.account, folder=self.folder, imap_uid=2,
            subject='Weekly digest', snippet='Here is your digest',
            from_address={'name': 'News', 'email': 'news@co.com'},
        )
        self.label_urgent = self.account.labels.get(name='Urgent')
        self.label_newsletter = self.account.labels.get(name='Newsletter')

    @patch('workspace.ai.client.get_ai_client')
    def test_classifies_messages_with_labels(self, mock_client):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([
            {'i': 1, 'labels': ['Urgent']},
            {'i': 2, 'labels': ['Newsletter']},
        ])
        mock_response.choices[0].message.tool_calls = None
        mock_response.model = 'gpt-4o-mini'
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_client.return_value.chat.completions.create.return_value = mock_response

        ai_task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.CLASSIFY,
            input_data={'message_uuids': [str(self.msg1.uuid), str(self.msg2.uuid)]},
        )
        from workspace.ai.tasks import classify_mail_messages
        classify_mail_messages(str(ai_task.uuid))

        from workspace.mail.models import MailMessageLabel
        self.assertTrue(MailMessageLabel.objects.filter(message=self.msg1, label=self.label_urgent).exists())
        self.assertTrue(MailMessageLabel.objects.filter(message=self.msg2, label=self.label_newsletter).exists())
        ai_task.refresh_from_db()
        self.assertEqual(ai_task.status, AITask.Status.COMPLETED)

    @patch('workspace.ai.client.get_ai_client')
    def test_unknown_label_names_ignored(self, mock_client):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([
            {'i': 1, 'labels': ['Urgent', 'NonExistent']},
        ])
        mock_response.choices[0].message.tool_calls = None
        mock_response.model = 'gpt-4o-mini'
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_client.return_value.chat.completions.create.return_value = mock_response

        ai_task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.CLASSIFY,
            input_data={'message_uuids': [str(self.msg1.uuid)]},
        )
        from workspace.ai.tasks import classify_mail_messages
        classify_mail_messages(str(ai_task.uuid))

        from workspace.mail.models import MailMessageLabel
        self.assertEqual(self.msg1.message_labels.count(), 1)
        self.assertEqual(self.msg1.message_labels.first().label.name, 'Urgent')

    @patch('workspace.ai.client.get_ai_client')
    def test_case_insensitive_label_matching(self, mock_client):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([
            {'i': 1, 'labels': ['urgent', 'NEWSLETTER']},
        ])
        mock_response.choices[0].message.tool_calls = None
        mock_response.model = 'gpt-4o-mini'
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_client.return_value.chat.completions.create.return_value = mock_response

        ai_task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.CLASSIFY,
            input_data={'message_uuids': [str(self.msg1.uuid)]},
        )
        from workspace.ai.tasks import classify_mail_messages
        classify_mail_messages(str(ai_task.uuid))

        from workspace.mail.models import MailMessageLabel
        self.assertEqual(self.msg1.message_labels.count(), 2)

    @patch('workspace.ai.client.get_ai_client')
    def test_max_3_labels_per_message(self, mock_client):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([
            {'i': 1, 'labels': ['Urgent', 'Action', 'FYI', 'Newsletter', 'Notification']},
        ])
        mock_response.choices[0].message.tool_calls = None
        mock_response.model = 'gpt-4o-mini'
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_client.return_value.chat.completions.create.return_value = mock_response

        ai_task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.CLASSIFY,
            input_data={'message_uuids': [str(self.msg1.uuid)]},
        )
        from workspace.ai.tasks import classify_mail_messages
        classify_mail_messages(str(ai_task.uuid))

        from workspace.mail.models import MailMessageLabel
        self.assertEqual(self.msg1.message_labels.count(), 3)

    @patch('workspace.ai.client.get_ai_client')
    def test_malformed_json_fails_gracefully(self, mock_client):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = 'not json'
        mock_response.choices[0].message.tool_calls = None
        mock_response.model = 'gpt-4o-mini'
        mock_response.usage.prompt_tokens = 50
        mock_response.usage.completion_tokens = 5
        mock_client.return_value.chat.completions.create.return_value = mock_response

        ai_task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.CLASSIFY,
            input_data={'message_uuids': [str(self.msg1.uuid)]},
        )
        from workspace.ai.tasks import classify_mail_messages
        result = classify_mail_messages(str(ai_task.uuid))

        self.assertEqual(result['status'], 'error')
        ai_task.refresh_from_db()
        self.assertEqual(ai_task.status, 'failed')
