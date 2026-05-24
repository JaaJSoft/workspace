from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.chat.models import (
    Conversation, ConversationMember, Message, MessageInteraction,
)
from workspace.chat.sse_provider import ChatSSEProvider

User = get_user_model()


class InteractionSSETests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='alice', email='a@test.com', password='pw',
        )
        self.bot = User.objects.create_user(
            username='bot', email='b@test.com', password='pw',
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.bot)
        self.msg = Message.objects.create(
            conversation=self.conv, author=self.bot, body='Q?',
        )

    def test_emits_event_when_other_user_answers(self):
        interaction = MessageInteraction.objects.create(
            message=self.msg,
            kind=MessageInteraction.Kind.QUESTION,
            payload={'question': 'Q', 'options': ['A', 'B']},
        )
        provider = ChatSSEProvider(self.user, last_event_id=None)
        other = User.objects.create_user(
            username='bob', email='b@x.com', password='pw',
        )
        ConversationMember.objects.create(conversation=self.conv, user=other)
        interaction.interacted_at = timezone.now()
        interaction.interacted_by = other
        interaction.state = {'selected_index': 0, 'answer_message_id': 'abc'}
        interaction.save()

        events = provider.poll(cache_value='dirty')
        event_types = [e[0] for e in events]
        self.assertIn('message_interaction_updated', event_types)
        # Idempotence: a second poll should not re-emit
        events2 = provider.poll(cache_value='dirty')
        self.assertNotIn(
            'message_interaction_updated',
            [e[0] for e in events2],
        )

    def test_does_not_emit_when_self_answered(self):
        interaction = MessageInteraction.objects.create(
            message=self.msg,
            kind=MessageInteraction.Kind.QUESTION,
            payload={'question': 'Q', 'options': ['A', 'B']},
        )
        provider = ChatSSEProvider(self.user, last_event_id=None)
        interaction.interacted_at = timezone.now()
        interaction.interacted_by = self.user
        interaction.state = {'selected_index': 0, 'answer_message_id': 'abc'}
        interaction.save()
        events = provider.poll(cache_value='dirty')
        self.assertNotIn(
            'message_interaction_updated',
            [e[0] for e in events],
        )
