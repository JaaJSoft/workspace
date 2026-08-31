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
from workspace.chat.serializers import MessageSerializer

User = get_user_model()


class MessageSerializerInteractionTests(TestCase):
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
        self.message = Message.objects.create(
            conversation=self.conv,
            author=self.bot,
            body="Quel ton ?",
        )

    def test_interaction_is_null_when_absent(self):
        data = MessageSerializer(self.message).data
        self.assertIn("interaction", data)
        self.assertIsNone(data["interaction"])

    def test_interaction_serialized_when_present(self):
        MessageInteraction.objects.create(
            message=self.message,
            kind=MessageInteraction.Kind.QUESTION,
            payload={"question": "Q", "options": ["A", "B"]},
        )
        self.message.refresh_from_db()
        data = MessageSerializer(self.message).data
        self.assertIsNotNone(data["interaction"])
        self.assertEqual(data["interaction"]["kind"], "question")
        self.assertEqual(data["interaction"]["payload"]["options"], ["A", "B"])
        self.assertIsNone(data["interaction"]["interacted_at"])
        self.assertIsNone(data["interaction"]["state"])

    def test_interaction_answered_state(self):
        interaction = MessageInteraction.objects.create(
            message=self.message,
            kind=MessageInteraction.Kind.QUESTION,
            payload={"question": "Q", "options": ["A", "B"]},
        )
        interaction.interacted_at = timezone.now()
        interaction.interacted_by = self.user
        interaction.state = {"selected_index": 0, "answer_message_id": "abc"}
        interaction.save()
        self.message.refresh_from_db()
        data = MessageSerializer(self.message).data
        self.assertIsNotNone(data["interaction"]["interacted_at"])
        self.assertEqual(data["interaction"]["state"]["selected_index"], 0)
        self.assertEqual(data["interaction"]["interacted_by"]["username"], "alice")


class MessageSerializerAuthorTests(TestCase):
    """Pins the identity resolver wiring: `author` is emitted for a member
    and a guest alike, under the same key so the frontend needs no change."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="alice",
            email="a2@test.com",
            password="pw",
            first_name="Alice",
            last_name="W",
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)

    def test_member_author_is_unchanged(self):
        msg = Message.objects.create(
            conversation=self.conv, author=self.user, body="hi"
        )
        data = MessageSerializer(msg).data
        self.assertEqual(data["author"]["id"], self.user.id)
        self.assertEqual(data["author"]["username"], "alice")
        self.assertEqual(data["author"]["display_name"], "Alice W")
        self.assertFalse(data["author"]["is_guest"])

    def test_guest_author_renders_the_guest_display_name(self):
        cal = Calendar.objects.create(name="C", owner=self.user)
        event = Event.objects.create(
            calendar=cal, owner=self.user, title="E", start=timezone.now()
        )
        meeting = Meeting.objects.create(
            event=event, conversation=self.conv, created_by=self.user
        )
        guest = MeetingGuest.objects.create(
            meeting=meeting,
            display_name="Visitor",
            occurrence_start=timezone.now(),
            token_hash="a" * 64,
        )
        msg = Message.objects.create(
            conversation=self.conv, guest=guest, body="hi from a guest"
        )

        data = MessageSerializer(msg).data

        self.assertIsNone(data["author"]["id"])
        self.assertEqual(data["author"]["display_name"], "Visitor")
        self.assertTrue(data["author"]["is_guest"])
