import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from PIL import Image

from workspace.ai.models import AITask
from workspace.ai.services.responses import post_bot_message
from workspace.chat.models import (
    Conversation,
    ConversationMember,
    MessageAttachment,
    MessageInteraction,
)

User = get_user_model()


class PostBotMessageInteractionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice",
            email="a@test.com",
            password="pw",
        )
        self.bot = User.objects.create_user(
            username="bot",
            email="b@test.com",
            password="pw",
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.bot)
        self.ai_task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.CHAT,
        )
        self.result = {
            "content": "Quel ton ?",
            "model": "test",
            "prompt_tokens": 1,
            "completion_tokens": 1,
        }

    def test_creates_interaction_when_context_has_question(self):
        tool_context = {
            "question": {
                "question": "Quel ton ?",
                "options": ["Formal", "Casual"],
            },
        }
        body, msg = post_bot_message(
            conversation=self.conv,
            bot_user=self.bot,
            result=self.result,
            tool_context=tool_context,
            ai_task=self.ai_task,
        )
        interaction = MessageInteraction.objects.get(message=msg)
        self.assertEqual(interaction.kind, MessageInteraction.Kind.QUESTION)
        self.assertEqual(interaction.payload["question"], "Quel ton ?")
        self.assertEqual(interaction.payload["options"], ["Formal", "Casual"])
        self.assertIsNone(interaction.interacted_at)

    def test_no_interaction_when_context_missing_question(self):
        body, msg = post_bot_message(
            conversation=self.conv,
            bot_user=self.bot,
            result=self.result,
            tool_context={},
            ai_task=self.ai_task,
        )
        self.assertFalse(MessageInteraction.objects.filter(message=msg).exists())


def _image_bytes(fmt):
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color="red").save(buf, format=fmt)
    return buf.getvalue()


class PostBotMessageImageTypingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice",
            email="a@test.com",
            password="pw",
        )
        self.bot = User.objects.create_user(
            username="bot",
            email="b@test.com",
            password="pw",
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.bot)
        self.ai_task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.CHAT,
        )
        self.result = {
            "content": "Voilà l'image",
            "model": "test",
            "prompt_tokens": 1,
            "completion_tokens": 1,
        }

    def _post(self, images):
        return post_bot_message(
            conversation=self.conv,
            bot_user=self.bot,
            result=self.result,
            tool_context={"images": images},
            ai_task=self.ai_task,
        )

    def test_generated_png_is_typed_from_content(self):
        _, msg = self._post([{"data": _image_bytes("PNG"), "prompt": "a red square"}])

        att = MessageAttachment.objects.get(message=msg)
        self.assertEqual(att.type, "png")
        self.assertEqual(att.category, "image")
        self.assertEqual(att.mime_type, "image/png")
        self.assertTrue(att.original_name.endswith(".png"))
        self.assertTrue(att.is_image)

    def test_generated_jpeg_is_not_mislabelled_as_png(self):
        _, msg = self._post([{"data": _image_bytes("JPEG"), "prompt": "a red square"}])

        att = MessageAttachment.objects.get(message=msg)
        self.assertEqual(att.type, "jpeg")
        self.assertEqual(att.category, "image")
        self.assertEqual(att.mime_type, "image/jpeg")
        self.assertFalse(att.original_name.endswith(".png"))

    def test_undetectable_bytes_keep_the_png_fallback(self):
        _, msg = self._post([{"data": b"", "prompt": "broken"}])

        att = MessageAttachment.objects.get(message=msg)
        self.assertEqual(att.type, "unknown")
        self.assertEqual(att.category, "unknown")
        self.assertEqual(att.mime_type, "image/png")
        self.assertTrue(att.is_image)


class PostBotMessageThinkingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="dave", email="d@test.com", password="pw"
        )
        self.bot = User.objects.create_user(
            username="bot3", email="b3@test.com", password="pw"
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.bot)
        self.ai_task = AITask.objects.create(
            owner=self.user, task_type=AITask.TaskType.CHAT
        )

    def _result(self, thinking=""):
        return {
            "content": "Hello",
            "thinking": thinking,
            "model": "test",
            "prompt_tokens": 1,
            "completion_tokens": 1,
        }

    def _post(self, result, tool_data=None):
        _, msg = post_bot_message(
            conversation=self.conv,
            bot_user=self.bot,
            result=result,
            tool_context={},
            ai_task=self.ai_task,
            tool_data=tool_data,
        )
        return msg

    def _round(self, thinking):
        return {
            "assistant_content": "",
            "thinking": thinking,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "t", "arguments": "{}"},
                }
            ],
            "results": [{"tool_call_id": "c1", "content": "ok"}],
        }

    def test_final_thinking_creates_tool_data_when_none(self):
        msg = self._post(self._result(thinking="final reasoning"))
        self.assertEqual(
            msg.tool_data,
            [{"thinking": "final reasoning", "tool_calls": [], "results": []}],
        )

    def test_final_thinking_appends_to_existing_rounds(self):
        msg = self._post(
            self._result(thinking="final reasoning"),
            tool_data=[self._round("round thinking")],
        )
        self.assertEqual(len(msg.tool_data), 2)
        self.assertEqual(msg.tool_data[-1]["thinking"], "final reasoning")
        self.assertEqual(msg.tool_data[-1]["tool_calls"], [])

    def test_no_thinking_leaves_tool_data_untouched(self):
        msg = self._post(self._result(thinking=""))
        self.assertIsNone(msg.tool_data)

    def test_duplicate_of_last_round_thinking_is_not_appended(self):
        # stop_after_round case: the posted result IS the last tool round,
        # whose thinking is already persisted in tool_data.
        msg = self._post(
            self._result(thinking="same thought"),
            tool_data=[self._round("same thought")],
        )
        self.assertEqual(len(msg.tool_data), 1)


class PostBotMessageCaptionEnqueueTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="erin", email="e@test.com", password="pw"
        )
        self.bot_user = User.objects.create_user(
            username="bot4", email="b4@test.com", password="pw"
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.bot_user)
        self.ai_task = AITask.objects.create(
            owner=self.user, task_type=AITask.TaskType.CHAT
        )

    @override_settings(AI_API_KEY="k")
    def test_generated_images_enqueue_captions(self):
        result = {
            "content": "here is your image",
            "model": "m",
            "prompt_tokens": 1,
            "completion_tokens": 1,
        }
        tool_context = {"images": [{"data": b"\x89PNGfake", "prompt": "a cat"}]}
        with patch(
            "workspace.ai.tasks.captions.generate_attachment_caption.delay"
        ) as mock_delay:
            with self.captureOnCommitCallbacks(execute=True):
                post_bot_message(
                    self.conv, self.bot_user, result, tool_context, self.ai_task
                )
        mock_delay.assert_called_once()
