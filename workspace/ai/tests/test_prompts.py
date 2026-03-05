from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.ai.models import BotProfile, UserMemory
from workspace.ai.prompts.chat import build_chat_messages

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
        self.assertIn('What you remember about this user', system)
        self.assertIn('name: Pierre', system)
        self.assertIn('lang: Python', system)
