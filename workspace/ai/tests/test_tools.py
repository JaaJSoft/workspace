import json
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.ai.models import BotProfile, UserMemory
from workspace.ai.tool_registry import tool_registry
from workspace.chat.models import Conversation, ConversationMember, Message

User = get_user_model()


class ChatToolDefinitionTests(TestCase):
    def test_tools_are_registered(self):
        definitions = tool_registry.get_definitions()
        self.assertIsInstance(definitions, list)
        names = [t["function"]["name"] for t in definitions]
        self.assertIn("save_memory", names)
        self.assertIn("delete_memory", names)
        self.assertIn("search_messages", names)
        self.assertIn("get_current_user_info", names)
        self.assertIn("get_weather", names)


class ExecuteToolCallTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="user", password="pass123")
        self.bot_user = User.objects.create_user(username="bot", password="pass123")
        BotProfile.objects.create(user=self.bot_user)

    def test_save_memory_creates(self):
        tool_call = MagicMock()
        tool_call.id = "call_1"
        tool_call.function.name = "save_memory"
        tool_call.function.arguments = json.dumps({"key": "name", "content": "Pierre"})

        result = tool_registry.execute(tool_call, user=self.user, bot=self.bot_user)

        self.assertIn("Saved", result)
        mem = UserMemory.objects.get(user=self.user, bot=self.bot_user, key="name")
        self.assertEqual(mem.content, "Pierre")

    def test_save_memory_updates_existing(self):
        UserMemory.objects.create(
            user=self.user, bot=self.bot_user, key="name", content="Pierre"
        )

        tool_call = MagicMock()
        tool_call.id = "call_2"
        tool_call.function.name = "save_memory"
        tool_call.function.arguments = json.dumps({"key": "name", "content": "Paul"})

        tool_registry.execute(tool_call, user=self.user, bot=self.bot_user)

        mem = UserMemory.objects.get(user=self.user, bot=self.bot_user, key="name")
        self.assertEqual(mem.content, "Paul")

    def test_delete_memory(self):
        UserMemory.objects.create(
            user=self.user, bot=self.bot_user, key="name", content="Pierre"
        )

        tool_call = MagicMock()
        tool_call.id = "call_3"
        tool_call.function.name = "delete_memory"
        tool_call.function.arguments = json.dumps({"key": "name"})

        result = tool_registry.execute(tool_call, user=self.user, bot=self.bot_user)

        self.assertIn("Deleted", result)
        self.assertFalse(
            UserMemory.objects.filter(
                user=self.user, bot=self.bot_user, key="name"
            ).exists()
        )

    def test_delete_memory_not_found(self):
        tool_call = MagicMock()
        tool_call.id = "call_4"
        tool_call.function.name = "delete_memory"
        tool_call.function.arguments = json.dumps({"key": "nonexistent"})

        result = tool_registry.execute(tool_call, user=self.user, bot=self.bot_user)

        self.assertIn("not found", result.lower())

    def test_unknown_tool(self):
        tool_call = MagicMock()
        tool_call.id = "call_5"
        tool_call.function.name = "unknown_tool"
        tool_call.function.arguments = "{}"

        result = tool_registry.execute(tool_call, user=self.user, bot=self.bot_user)

        self.assertIn("Unknown", result)

    def test_search_messages(self):
        conv = Conversation.objects.create(created_by=self.user)
        ConversationMember.objects.create(conversation=conv, user=self.user)
        Message.objects.create(conversation=conv, author=self.user, body="Hello world")
        Message.objects.create(
            conversation=conv, author=self.user, body="Goodbye world"
        )
        Message.objects.create(conversation=conv, author=self.user, body="Nothing here")

        tool_call = MagicMock()
        tool_call.id = "call_6"
        tool_call.function.name = "search_messages"
        tool_call.function.arguments = json.dumps({"query": "world"})

        result = tool_registry.execute(
            tool_call, user=self.user, bot=self.bot_user, conversation_id=str(conv.pk)
        )

        self.assertIn("Hello world", result)
        self.assertIn("Goodbye world", result)
        self.assertNotIn("Nothing here", result)

    def test_search_messages_no_results(self):
        conv = Conversation.objects.create(created_by=self.user)

        tool_call = MagicMock()
        tool_call.id = "call_7"
        tool_call.function.name = "search_messages"
        tool_call.function.arguments = json.dumps({"query": "nonexistent"})

        result = tool_registry.execute(
            tool_call, user=self.user, bot=self.bot_user, conversation_id=str(conv.pk)
        )

        self.assertIn("No messages found", result)

    @patch("workspace.ai.services.weather.get_current_weather")
    def test_get_weather(self, mock_weather):
        mock_weather.return_value = {
            "location": "Paris, France",
            "temperature": 15,
            "conditions": "Overcast",
        }

        tool_call = MagicMock()
        tool_call.id = "call_w1"
        tool_call.function.name = "get_weather"
        tool_call.function.arguments = json.dumps({"location": "Paris"})

        result = tool_registry.execute(tool_call, user=self.user, bot=self.bot_user)

        data = json.loads(result)
        self.assertEqual(data["location"], "Paris, France")
        self.assertEqual(data["conditions"], "Overcast")
        mock_weather.assert_called_once_with("Paris")

    @patch("workspace.ai.services.weather.get_current_weather")
    def test_get_weather_not_found(self, mock_weather):
        mock_weather.return_value = None

        tool_call = MagicMock()
        tool_call.id = "call_w2"
        tool_call.function.name = "get_weather"
        tool_call.function.arguments = json.dumps({"location": "Nowhereville"})

        result = tool_registry.execute(tool_call, user=self.user, bot=self.bot_user)

        self.assertIn("Could not find weather", result)

    def test_get_weather_missing_location(self):
        tool_call = MagicMock()
        tool_call.id = "call_w3"
        tool_call.function.name = "get_weather"
        tool_call.function.arguments = json.dumps({"location": "  "})

        result = tool_registry.execute(tool_call, user=self.user, bot=self.bot_user)

        self.assertIn("Error", result)

    def test_get_current_user_info(self):
        self.user.first_name = "Pierre"
        self.user.last_name = "Dupont"
        self.user.email = "pierre@example.com"
        self.user.save()

        tool_call = MagicMock()
        tool_call.id = "call_8"
        tool_call.function.name = "get_current_user_info"
        tool_call.function.arguments = "{}"

        result = tool_registry.execute(tool_call, user=self.user, bot=self.bot_user)

        data = json.loads(result)
        self.assertEqual(data["username"], "user")
        self.assertEqual(data["first_name"], "Pierre")
        self.assertEqual(data["last_name"], "Dupont")
        self.assertEqual(data["email"], "pierre@example.com")
