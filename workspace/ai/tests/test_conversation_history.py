import base64
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from workspace.ai.models import BotProfile
from workspace.ai.services.conversation_history import build_conversation_history
from workspace.chat.models import Conversation, Message, MessageAttachment

User = get_user_model()


class HistoryToolLessRoundsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="erin", email="e@test.com", password="pw"
        )
        self.bot_user = User.objects.create_user(
            username="histbot", email="hb@test.com", password="pw"
        )
        self.bot_profile = BotProfile.objects.create(user=self.bot_user)
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        Message.objects.create(conversation=self.conv, author=self.user, body="hi")

    def test_thinking_only_round_is_skipped_not_replayed(self):
        Message.objects.create(
            conversation=self.conv,
            author=self.bot_user,
            body="Hello!",
            tool_data=[
                {
                    "assistant_content": "",
                    "thinking": "round thinking",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "t", "arguments": "{}"},
                        }
                    ],
                    "results": [{"tool_call_id": "c1", "content": "ok"}],
                },
                {"thinking": "final secret reasoning", "tool_calls": [], "results": []},
            ],
        )
        history, _ = build_conversation_history(
            self.conv.pk, self.bot_profile, self.user
        )
        # No assistant message with empty tool_calls, no thinking anywhere.
        for entry in history:
            self.assertNotEqual(entry.get("tool_calls"), [])
            self.assertNotIn("secret", str(entry.get("content", "")))
        # The real tool round is still reconstructed.
        tool_rounds = [e for e in history if e.get("tool_calls")]
        self.assertEqual(len(tool_rounds), 1)

    def test_round_missing_tool_calls_key_does_not_crash(self):
        Message.objects.create(
            conversation=self.conv,
            author=self.bot_user,
            body="Hello!",
            tool_data=[{"thinking": "only reasoning"}],
        )
        history, _ = build_conversation_history(
            self.conv.pk, self.bot_profile, self.user
        )
        self.assertTrue(any(e.get("content") == "Hello!" for e in history))


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABh6FO1AAAAABJRU5ErkJggg=="
)


def attach_image(message, name="pic.png", ai_description=""):
    att = MessageAttachment(
        message=message,
        original_name=name,
        mime_type="image/png",
        type="png",
        category="image",
        size=len(PNG_BYTES),
        ai_description=ai_description,
    )
    att.file.save(name, ContentFile(PNG_BYTES), save=False)
    att.save()
    return att


def image_parts(entry):
    content = entry.get("content")
    if not isinstance(content, list):
        return []
    return [p for p in content if isinstance(p, dict) and p.get("type") == "image_url"]


class VisualWindowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="visu", email="v@test.com", password="pw"
        )
        self.bot_user = User.objects.create_user(
            username="visbot", email="vb@test.com", password="pw"
        )
        self.bot_profile = BotProfile.objects.create(
            user=self.bot_user, supports_vision=True
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )

    def _history(self):
        history, _ = build_conversation_history(
            self.conv.pk, self.bot_profile, self.user
        )
        return history

    def test_two_visual_messages_get_pixels_including_bot(self):
        m1 = Message.objects.create(
            conversation=self.conv, author=self.bot_user, body="here you go"
        )
        attach_image(m1, "generated.png")
        m2 = Message.objects.create(
            conversation=self.conv, author=self.user, body="and mine"
        )
        attach_image(m2, "upload.png")
        history = self._history()
        with_pixels = [e for e in history if image_parts(e)]
        self.assertEqual(len(with_pixels), 2)
        # Bot images must never ride in an assistant-role message.
        for entry in with_pixels:
            self.assertEqual(entry["role"], "user")

    def test_third_visual_message_degrades_to_caption(self):
        old = Message.objects.create(
            conversation=self.conv, author=self.user, body="old"
        )
        attach_image(old, "old.png", ai_description="A sunset over the sea.")
        for i in range(2):
            m = Message.objects.create(
                conversation=self.conv, author=self.user, body=f"new {i}"
            )
            attach_image(m, f"new{i}.png")
        history = self._history()
        flat = str(history)
        self.assertIn("[image: old.png - A sunset over the sea.]", flat)
        self.assertEqual(len([e for e in history if image_parts(e)]), 2)

    def test_missing_caption_falls_back_and_reenqueues(self):
        old = Message.objects.create(
            conversation=self.conv, author=self.user, body="old"
        )
        att = attach_image(old, "old.png")
        for i in range(2):
            m = Message.objects.create(
                conversation=self.conv, author=self.user, body=f"new {i}"
            )
            attach_image(m, f"new{i}.png")
        with patch(
            "workspace.ai.tasks.captions.generate_attachment_caption.delay"
        ) as mock_delay:
            history = self._history()
        self.assertIn("[image: old.png]", str(history))
        mock_delay.assert_called_once_with(str(att.uuid))

    @override_settings(AI_VISION_MAX_IMAGES=1)
    def test_image_cap_prefers_newest(self):
        m1 = Message.objects.create(
            conversation=self.conv, author=self.user, body="first"
        )
        attach_image(m1, "first.png")
        m2 = Message.objects.create(
            conversation=self.conv, author=self.user, body="second"
        )
        attach_image(m2, "second.png")
        # first.png has no caption: its note re-enqueues the caption task,
        # which must be mocked so the test never touches the Celery broker.
        with patch("workspace.ai.tasks.captions.generate_attachment_caption.delay"):
            history = self._history()
        self.assertEqual(len([e for e in history if image_parts(e)]), 1)
        self.assertIn("[image: first.png]", str(history))

    def test_non_vision_bot_unchanged(self):
        self.bot_profile.supports_vision = False
        self.bot_profile.save()
        m = Message.objects.create(
            conversation=self.conv, author=self.user, body="look"
        )
        attach_image(m, "pic.png")
        with patch(
            "workspace.ai.tasks.captions.generate_attachment_caption.delay"
        ) as mock_delay:
            history = self._history()
        self.assertEqual([e for e in history if image_parts(e)], [])
        self.assertNotIn("[image:", str(history))
        mock_delay.assert_not_called()
