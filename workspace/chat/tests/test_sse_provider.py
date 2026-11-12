import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    LinkPreview,
    Message,
    MessageInteraction,
    MessageLinkPreview,
    PinnedMessage,
    Reaction,
)
from workspace.chat.sse_provider import ChatSSEProvider
from workspace.common.tests.parameter_limit import sqlite_parameter_limit

User = get_user_model()

PARAMETER_LIMIT = 40


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


class LongLivedStreamTests(TestCase):
    """A stream that already delivered more items than SQLite binds
    parameters still delivers the next one."""

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.other = User.objects.create_user(username="bob", password="pw")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.other)
        self.own_message = Message.objects.create(
            conversation=self.conv, author=self.user, body="Mine"
        )
        self.provider = ChatSSEProvider(self.user, last_event_id=None)

    def _already_delivered(self):
        return {uuid.uuid4() for _ in range(PARAMETER_LIMIT * 2)}

    def _poll(self, event_type):
        with sqlite_parameter_limit(PARAMETER_LIMIT):
            events = self.provider.poll(cache_value="dirty")
        return [data for name, data, _ in events if name == event_type]

    def test_new_message_after_many_messages(self):
        self.provider._seen_message_ids |= self._already_delivered()
        message = Message.objects.create(
            conversation=self.conv, author=self.other, body="One more"
        )

        delivered = self._poll("message")

        self.assertEqual(
            [data["message"]["uuid"] for data in delivered], [str(message.uuid)]
        )

    def test_new_reaction_after_many_reactions(self):
        self.provider._seen_reaction_ids |= self._already_delivered()
        Reaction.objects.create(message=self.own_message, user=self.other, emoji="+1")

        delivered = self._poll("reaction")

        self.assertEqual(
            [data["message_id"] for data in delivered], [str(self.own_message.uuid)]
        )

    def test_new_pin_after_many_pins(self):
        self.provider._seen_pin_ids |= self._already_delivered()
        PinnedMessage.objects.create(
            conversation=self.conv, message=self.own_message, pinned_by=self.other
        )

        delivered = self._poll("message_pinned")

        self.assertEqual(
            [data["message_id"] for data in delivered], [str(self.own_message.uuid)]
        )

    def test_new_link_preview_after_many_link_previews(self):
        self.provider._seen_link_preview_ids |= self._already_delivered()
        preview = LinkPreview.objects.create(url="https://example.com/article")
        MessageLinkPreview.objects.create(message=self.own_message, preview=preview)

        delivered = self._poll("link_preview")

        self.assertEqual(
            [data["conversation_id"] for data in delivered], [str(self.conv.uuid)]
        )
