from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.ai.models import BotProfile, UserMemory
from workspace.ai.prompts.chat import build_chat_messages
from workspace.ai.prompts.mail import build_classify_messages

User = get_user_model()


class BuildChatMessagesMemoryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='user', password='pass123')
        self.bot_user = User.objects.create_user(username='bot', password='pass123')
        BotProfile.objects.create(user=self.bot_user)

    def test_no_memories_no_section(self):
        msgs = build_chat_messages('System prompt', [], bot_name='Bot')
        system = msgs[0]['content']
        self.assertNotIn('What you remember', system)

    def test_memories_injected(self):
        UserMemory.objects.create(user=self.user, bot=self.bot_user, key='name', content='Pierre')
        UserMemory.objects.create(user=self.user, bot=self.bot_user, key='lang', content='Python')

        msgs = build_chat_messages(
            'System prompt', [], bot_name='Bot',
            user=self.user, bot=self.bot_user,
        )
        system = msgs[0]['content']
        self.assertIn('User context', system)
        self.assertIn('name: Pierre', system)
        self.assertIn('lang: Python', system)


class BuildClassifyMessagesTests(TestCase):
    def test_builds_messages_with_labels(self):
        emails = [
            {'subject': 'Test', 'from_name': 'Alice', 'from_email': 'a@b.com', 'snippet': 'Hello'},
        ]
        labels = ['Urgent', 'Action', 'Newsletter']
        result = build_classify_messages(emails, labels)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['role'], 'system')
        self.assertIn('Urgent', result[0]['content'])
        self.assertIn('Action', result[0]['content'])
        self.assertIn('Newsletter', result[0]['content'])
        self.assertIn('"labels"', result[0]['content'])
        self.assertIn('[1]', result[1]['content'])
        self.assertIn('Test', result[1]['content'])

    def test_injection_guard_present(self):
        emails = [{'subject': 'X', 'from_name': '', 'from_email': 'x@y.com', 'snippet': ''}]
        result = build_classify_messages(emails, ['Urgent'])
        self.assertIn('untrusted-content', result[1]['content'])

    def test_empty_labels_list(self):
        emails = [{'subject': 'X', 'from_name': '', 'from_email': 'x@y.com', 'snippet': ''}]
        result = build_classify_messages(emails, [])
        self.assertEqual(len(result), 2)
