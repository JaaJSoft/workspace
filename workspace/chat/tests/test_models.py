from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase

from workspace.chat.models import (
    Conversation, ConversationMember, Message, MessageInteraction,
)

User = get_user_model()


class MessageInteractionModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='alice', email='a@test.com', password='pw',
        )
        self.bot = User.objects.create_user(
            username='bot', email='b@test.com', password='pw',
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.bot)
        self.message = Message.objects.create(
            conversation=self.conv, author=self.bot, body='Pick a tone:',
        )

    def test_create_question_interaction(self):
        interaction = MessageInteraction.objects.create(
            message=self.message,
            kind=MessageInteraction.Kind.QUESTION,
            payload={'question': 'Pick a tone', 'options': ['Formal', 'Casual']},
        )
        self.assertEqual(interaction.kind, 'question')
        self.assertEqual(interaction.payload['options'], ['Formal', 'Casual'])
        self.assertIsNone(interaction.interacted_at)
        self.assertIsNone(interaction.state)

    def test_one_to_one_constraint(self):
        MessageInteraction.objects.create(
            message=self.message,
            kind=MessageInteraction.Kind.QUESTION,
            payload={'question': 'q', 'options': ['a', 'b']},
        )
        with self.assertRaises(IntegrityError):
            MessageInteraction.objects.create(
                message=self.message,
                kind=MessageInteraction.Kind.QUESTION,
                payload={'question': 'q2', 'options': ['x', 'y']},
            )

    def test_cascade_delete_with_message(self):
        MessageInteraction.objects.create(
            message=self.message,
            kind=MessageInteraction.Kind.QUESTION,
            payload={'question': 'q', 'options': ['a', 'b']},
        )
        self.message.delete()
        self.assertEqual(MessageInteraction.objects.count(), 0)

    def test_reverse_accessor_on_message(self):
        interaction = MessageInteraction.objects.create(
            message=self.message,
            kind=MessageInteraction.Kind.QUESTION,
            payload={'question': 'q', 'options': ['a', 'b']},
        )
        self.message.refresh_from_db()
        self.assertEqual(self.message.interaction, interaction)

    def test_interacted_by_set_null_on_user_delete(self):
        # Use an outsider user so deleting them does not cascade through
        # ConversationMember.user / Conversation.created_by / Message.author
        # and wipe the interaction before we can check it.
        outsider = User.objects.create_user(
            username='outsider', email='o@test.com', password='pw',
        )
        interaction = MessageInteraction.objects.create(
            message=self.message,
            kind=MessageInteraction.Kind.QUESTION,
            payload={'question': 'q', 'options': ['a', 'b']},
        )
        interaction.interacted_by = outsider
        interaction.interacted_at = None  # leave unanswered
        interaction.save()
        outsider.delete()
        interaction.refresh_from_db()
        self.assertIsNone(interaction.interacted_by)
