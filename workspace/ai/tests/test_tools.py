import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings

from workspace.ai.models import BotProfile, UserMemory
from workspace.ai.tool_registry import tool_registry
from workspace.ai.tools import GenerateImageParams, ImageToolProvider
from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.common.search import fts5_available

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

    def test_search_messages_ranked_by_relevance(self):
        # top is created first (older) but far more relevant; a plain
        # -created_at ordering would incorrectly rank it second.
        if connection.vendor == "sqlite" and not fts5_available():
            self.skipTest("relevance ranking needs FTS5 on SQLite")
        conv = Conversation.objects.create(created_by=self.user)
        ConversationMember.objects.create(conversation=conv, user=self.user)
        Message.objects.create(
            conversation=conv, author=self.user, body="duckling duckling duckling"
        )
        Message.objects.create(conversation=conv, author=self.user, body="duckling")

        tool_call = MagicMock()
        tool_call.id = "call_6b"
        tool_call.function.name = "search_messages"
        tool_call.function.arguments = json.dumps({"query": "duckling"})

        result = tool_registry.execute(
            tool_call, user=self.user, bot=self.bot_user, conversation_id=str(conv.pk)
        )

        data = json.loads(result)
        self.assertEqual(data[0]["body"], "duckling duckling duckling")

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


class CurrentUserInfoTimezoneTests(TestCase):
    def tearDown(self):
        cache.clear()

    def test_date_joined_in_user_timezone(self):
        from workspace.ai.tools import CoreToolProvider
        from workspace.users.services.settings import set_setting

        user = User.objects.create_user(username="tzju", password="pw")
        # 23:30 UTC on Jan 31 is already Feb 1 in Paris.
        User.objects.filter(pk=user.pk).update(
            date_joined=datetime(2026, 1, 31, 23, 30, tzinfo=UTC)
        )
        user.refresh_from_db()
        set_setting(user, "core", "timezone", "Europe/Paris")
        result = CoreToolProvider().get_current_user_info(
            None, user=user, bot=None, conversation_id=None, context={}
        )
        self.assertIn("2026-02-01", result)


class EditImageSameTurnTests(TestCase):
    """Regression: edit_image must edit the image generated earlier in the
    same tool-loop turn (context['images']), not the last DB attachment."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="editu", email="editu@test.com", password="pw"
        )
        self.bot_user = User.objects.create_user(
            username="editbot", email="editbot@test.com", password="pw"
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )

    def test_edit_uses_current_turn_image_over_db(self):
        from workspace.ai.tools import EditImageParams

        provider = ImageToolProvider()
        args = EditImageParams(prompt="make it darker")
        context = {
            "images": [{"data": b"TURN_IMAGE", "prompt": "cat", "size": "1024x1024"}]
        }
        with patch(
            "workspace.ai.services.image.ai_edit_image", return_value=b"EDITED"
        ) as mock_edit:
            result = provider.edit_image(
                args, self.user, self.bot_user, str(self.conv.pk), context
            )
        mock_edit.assert_called_once()
        self.assertEqual(mock_edit.call_args[0][0], b"TURN_IMAGE")
        self.assertIn("Image edited successfully", result)
        self.assertEqual(context["images"][-1]["data"], b"EDITED")


def _fake_image_client(b64):
    response = SimpleNamespace(data=[SimpleNamespace(b64_json=b64)])
    images = SimpleNamespace(
        generate=lambda **kwargs: response, edit=lambda **kwargs: response
    )
    return SimpleNamespace(images=images)


@override_settings(AI_IMAGE_MODEL="img-model")
class GenerateImageVisionTests(TestCase):
    PNG_B64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAA"
        "BQABh6FO1AAAAABJRU5ErkJggg=="
    )

    def setUp(self):
        self.user = User.objects.create_user(
            username="genu", email="genu@test.com", password="pw"
        )
        self.bot_user = User.objects.create_user(
            username="genbot", email="genbot@test.com", password="pw"
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )

    def _generate(self, context):
        provider = ImageToolProvider()
        args = GenerateImageParams(prompt="a cat")
        with patch(
            "workspace.ai.tools.get_image_client",
            return_value=_fake_image_client(self.PNG_B64),
        ):
            return provider.generate_image(
                args, self.user, self.bot_user, str(self.conv.pk), context
            )

    def test_vision_bot_gets_image_payload(self):
        BotProfile.objects.create(user=self.bot_user, supports_vision=True)
        context = {}
        result = self._generate(context)
        parsed = json.loads(result)
        self.assertEqual(parsed["type"], "image")
        self.assertEqual(parsed["text"], "Image generated successfully for: a cat")
        self.assertTrue(parsed["data"])
        self.assertTrue(parsed["mime_type"].startswith("image/"))
        self.assertEqual(len(context["images"]), 1)

    def test_non_vision_bot_gets_plain_text(self):
        BotProfile.objects.create(user=self.bot_user, supports_vision=False)
        result = self._generate({})
        self.assertEqual(result, "Image generated successfully for: a cat")

    def test_bot_without_profile_gets_plain_text(self):
        result = self._generate({})
        self.assertEqual(result, "Image generated successfully for: a cat")
