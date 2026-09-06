from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.ai.services.chat_summary import update_summary
from workspace.calendar.models import Calendar, Event
from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Meeting,
    MeetingGuest,
    Message,
)

User = get_user_model()


class UpdateSummaryGuestAuthorTests(TestCase):
    """A guest-authored message (no ``author`` row) must survive the
    summariser, the same way it already survives search and the AI tools."""

    def setUp(self):
        self.user = User.objects.create_user(username="host", password="x")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.user
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.user
        )
        cal = Calendar.objects.create(name="C", owner=self.user)
        event = Event.objects.create(
            calendar=cal, owner=self.user, title="Standup", start=timezone.now()
        )
        meeting = Meeting.objects.create(
            event=event, conversation=self.conversation, created_by=self.user
        )
        self.guest = MeetingGuest.objects.create(
            meeting=meeting,
            display_name="Visitor",
            occurrence_start=timezone.now(),
            token_hash="a" * 64,
        )

    @override_settings(AI_CHAT_CONTEXT_SIZE=1)
    @patch("workspace.ai.services.chat_summary.call_llm")
    def test_guest_authored_message_survives_summarisation(self, mock_call_llm):
        mock_call_llm.return_value = {
            "content": "summary text",
            "prompt_tokens": 1,
            "completion_tokens": 1,
        }
        # Older message (falls outside the AI_CHAT_CONTEXT_SIZE=1 window, so
        # it is the one actually summarised) is guest-authored.
        Message.objects.create(
            conversation=self.conversation, guest=self.guest, body="hello there"
        )
        Message.objects.create(
            conversation=self.conversation, author=self.user, body="hi"
        )

        result = update_summary(str(self.conversation.uuid))

        self.assertEqual(result["status"], "ok")
        prompt_messages = mock_call_llm.call_args[0][0]
        user_content = prompt_messages[1]["content"]
        self.assertIn("Visitor", user_content)
