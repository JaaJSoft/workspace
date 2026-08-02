from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.ai.models import BotProfile
from workspace.ai.services.conversation_history import build_conversation_history
from workspace.chat.models import Conversation, Message

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
