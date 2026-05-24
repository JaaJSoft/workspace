from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.chat.models import (
    Conversation, ConversationMember, Message, MessageInteraction,
)
from workspace.chat.serializers import MessageSerializer

User = get_user_model()


class MessageSerializerInteractionTests(TestCase):
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
            conversation=self.conv, author=self.bot, body='Quel ton ?',
        )

    def test_interaction_is_null_when_absent(self):
        data = MessageSerializer(self.message).data
        self.assertIn('interaction', data)
        self.assertIsNone(data['interaction'])

    def test_interaction_serialized_when_present(self):
        MessageInteraction.objects.create(
            message=self.message,
            kind=MessageInteraction.Kind.QUESTION,
            payload={'question': 'Q', 'options': ['A', 'B']},
        )
        self.message.refresh_from_db()
        data = MessageSerializer(self.message).data
        self.assertIsNotNone(data['interaction'])
        self.assertEqual(data['interaction']['kind'], 'question')
        self.assertEqual(data['interaction']['payload']['options'], ['A', 'B'])
        self.assertIsNone(data['interaction']['interacted_at'])
        self.assertIsNone(data['interaction']['state'])

    def test_interaction_answered_state(self):
        interaction = MessageInteraction.objects.create(
            message=self.message,
            kind=MessageInteraction.Kind.QUESTION,
            payload={'question': 'Q', 'options': ['A', 'B']},
        )
        interaction.interacted_at = timezone.now()
        interaction.interacted_by = self.user
        interaction.state = {'selected_index': 0, 'answer_message_id': 'abc'}
        interaction.save()
        self.message.refresh_from_db()
        data = MessageSerializer(self.message).data
        self.assertIsNotNone(data['interaction']['interacted_at'])
        self.assertEqual(data['interaction']['state']['selected_index'], 0)
        self.assertEqual(data['interaction']['interacted_by']['username'], 'alice')
