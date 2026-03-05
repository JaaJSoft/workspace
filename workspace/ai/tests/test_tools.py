import json
from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.ai.models import BotProfile, UserMemory
from workspace.ai.tools import CHAT_TOOLS, execute_tool_call

User = get_user_model()


class ChatToolDefinitionTests(TestCase):
    def test_tools_are_list(self):
        self.assertIsInstance(CHAT_TOOLS, list)
        names = [t['function']['name'] for t in CHAT_TOOLS]
        self.assertIn('save_memory', names)
        self.assertIn('delete_memory', names)


class ExecuteToolCallTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='user', password='pass123')
        self.bot_user = User.objects.create_user(username='bot', password='pass123')
        BotProfile.objects.create(user=self.bot_user)

    def test_save_memory_creates(self):
        tool_call = MagicMock()
        tool_call.id = 'call_1'
        tool_call.function.name = 'save_memory'
        tool_call.function.arguments = json.dumps({'key': 'name', 'content': 'Pierre'})

        result = execute_tool_call(tool_call, user=self.user, bot=self.bot_user)

        self.assertIn('Saved', result)
        mem = UserMemory.objects.get(user=self.user, bot=self.bot_user, key='name')
        self.assertEqual(mem.content, 'Pierre')

    def test_save_memory_updates_existing(self):
        UserMemory.objects.create(user=self.user, bot=self.bot_user, key='name', content='Pierre')

        tool_call = MagicMock()
        tool_call.id = 'call_2'
        tool_call.function.name = 'save_memory'
        tool_call.function.arguments = json.dumps({'key': 'name', 'content': 'Paul'})

        execute_tool_call(tool_call, user=self.user, bot=self.bot_user)

        mem = UserMemory.objects.get(user=self.user, bot=self.bot_user, key='name')
        self.assertEqual(mem.content, 'Paul')

    def test_delete_memory(self):
        UserMemory.objects.create(user=self.user, bot=self.bot_user, key='name', content='Pierre')

        tool_call = MagicMock()
        tool_call.id = 'call_3'
        tool_call.function.name = 'delete_memory'
        tool_call.function.arguments = json.dumps({'key': 'name'})

        result = execute_tool_call(tool_call, user=self.user, bot=self.bot_user)

        self.assertIn('Deleted', result)
        self.assertFalse(UserMemory.objects.filter(user=self.user, bot=self.bot_user, key='name').exists())

    def test_delete_memory_not_found(self):
        tool_call = MagicMock()
        tool_call.id = 'call_4'
        tool_call.function.name = 'delete_memory'
        tool_call.function.arguments = json.dumps({'key': 'nonexistent'})

        result = execute_tool_call(tool_call, user=self.user, bot=self.bot_user)

        self.assertIn('not found', result.lower())

    def test_unknown_tool(self):
        tool_call = MagicMock()
        tool_call.id = 'call_5'
        tool_call.function.name = 'unknown_tool'
        tool_call.function.arguments = '{}'

        result = execute_tool_call(tool_call, user=self.user, bot=self.bot_user)

        self.assertIn('Unknown', result)
