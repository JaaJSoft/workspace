from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.calendar.models import Calendar, Event
from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Meeting,
    MeetingGuest,
    Message,
    MessageInteraction,
)
from workspace.chat.sse_provider import ChatSSEProvider

User = get_user_model()


class InteractionSSETests(TestCase):
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
            kind=Conversation.Kind.GROUP,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.bot)
        self.msg = Message.objects.create(
            conversation=self.conv,
            author=self.bot,
            body="Q?",
        )

    def test_emits_event_when_other_user_answers(self):
        interaction = MessageInteraction.objects.create(
            message=self.msg,
            kind=MessageInteraction.Kind.QUESTION,
            payload={"question": "Q", "options": ["A", "B"]},
        )
        provider = ChatSSEProvider(self.user, last_event_id=None)
        other = User.objects.create_user(
            username="bob",
            email="b@x.com",
            password="pw",
        )
        ConversationMember.objects.create(conversation=self.conv, user=other)
        interaction.interacted_at = timezone.now()
        interaction.interacted_by = other
        interaction.state = {"selected_index": 0, "answer_message_id": "abc"}
        interaction.save()

        events = provider.poll(cache_value="dirty")
        event_types = [e[0] for e in events]
        self.assertIn("message_interaction_updated", event_types)
        # Idempotence: a second poll should not re-emit
        events2 = provider.poll(cache_value="dirty")
        self.assertNotIn(
            "message_interaction_updated",
            [e[0] for e in events2],
        )

    def test_emits_conversation_updated_on_title_change(self):
        provider = ChatSSEProvider(self.user, last_event_id=None)
        self.conv.title = "Fresh AI title"
        self.conv.save(update_fields=["title"])

        events = provider.poll(cache_value="dirty")
        updated = [e for e in events if e[0] == "conversation_updated"]
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0][1]["conversation_id"], str(self.conv.uuid))
        self.assertEqual(updated[0][1]["title"], "Fresh AI title")
        # Idempotence: a second poll should not re-emit
        events2 = provider.poll(cache_value="dirty")
        self.assertNotIn("conversation_updated", [e[0] for e in events2])

    def test_no_conversation_updated_when_title_unchanged(self):
        provider = ChatSSEProvider(self.user, last_event_id=None)
        events = provider.poll(cache_value="dirty")
        self.assertNotIn("conversation_updated", [e[0] for e in events])

    def test_does_not_emit_when_self_answered(self):
        interaction = MessageInteraction.objects.create(
            message=self.msg,
            kind=MessageInteraction.Kind.QUESTION,
            payload={"question": "Q", "options": ["A", "B"]},
        )
        provider = ChatSSEProvider(self.user, last_event_id=None)
        interaction.interacted_at = timezone.now()
        interaction.interacted_by = self.user
        interaction.state = {"selected_index": 0, "answer_message_id": "abc"}
        interaction.save()
        events = provider.poll(cache_value="dirty")
        self.assertNotIn(
            "message_interaction_updated",
            [e[0] for e in events],
        )


class GuestMessageSSETests(TestCase):
    """Pins the SSE feed's identity resolution: a guest-authored message must
    render the guest's display name instead of a null author."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="host", email="host@test.com", password="pw"
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        cal = Calendar.objects.create(name="C", owner=self.user)
        event = Event.objects.create(
            calendar=cal, owner=self.user, title="E", start=timezone.now()
        )
        meeting = Meeting.objects.create(
            event=event, conversation=self.conv, created_by=self.user
        )
        self.guest = MeetingGuest.objects.create(
            meeting=meeting,
            display_name="Visitor",
            occurrence_start=timezone.now(),
            token_hash="f" * 64,
        )

    def test_guest_authored_message_survives_the_sse_feed(self):
        provider = ChatSSEProvider(self.user, last_event_id=None)
        Message.objects.create(
            conversation=self.conv, guest=self.guest, body="hi from a guest"
        )

        events = provider.poll(cache_value="dirty")

        message_events = [e for e in events if e[0] == "message"]
        self.assertEqual(len(message_events), 1)
        author = message_events[0][1]["message"]["author"]
        self.assertIsNone(author["id"])
        self.assertEqual(author["display_name"], "Visitor")
        self.assertTrue(author["is_guest"])
