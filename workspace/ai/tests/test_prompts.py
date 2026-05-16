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

    def test_time_context_is_appended_after_history_not_in_system(self):
        # The volatile date/time block must live in a separate system
        # message AFTER the history so it doesn't invalidate the cached
        # system prompt prefix on every turn.
        history = [{'role': 'user', 'content': 'hi'}]
        msgs = build_chat_messages(
            'System prompt', history, bot_name='Bot', user=self.user,
        )
        # System prompt at index 0 must not contain the time block.
        self.assertNotIn('Current date:', msgs[0]['content'])
        self.assertNotIn('Current time:', msgs[0]['content'])
        # Identity stays in the cached prefix.
        self.assertIn(f'Your name is Bot.', msgs[0]['content'])
        self.assertIn(f'You are talking to', msgs[0]['content'])
        # The last message is a system reminder carrying the time block.
        last = msgs[-1]
        self.assertEqual(last['role'], 'system')
        self.assertIn('<context>', last['content'])
        self.assertIn('Current date:', last['content'])
        self.assertIn('Current time:', last['content'])

    def test_no_identity_block_when_no_bot_name_or_user(self):
        msgs = build_chat_messages('System prompt', [])
        system = msgs[0]['content']
        self.assertNotIn('Your name is', system)
        self.assertNotIn('You are talking to', system)
        # Time reminder still appended.
        self.assertEqual(msgs[-1]['role'], 'system')
        self.assertIn('<context>', msgs[-1]['content'])


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
