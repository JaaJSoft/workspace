from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import TestCase
from django.utils import timezone

from workspace.chat.models import (
    Conversation, ConversationMember, Message, MessageInteraction,
)

User = get_user_model()


class MessageInteractionPartialTests(TestCase):
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

    def _render(self, message):
        return render_to_string(
            'chat/ui/partials/_message_interaction.html',
            {'msg': message},
        )

    def test_pending_renders_clickable_buttons(self):
        MessageInteraction.objects.create(
            message=self.message,
            kind=MessageInteraction.Kind.QUESTION,
            payload={'question': 'Q', 'options': ['Formal', 'Casual']},
        )
        self.message.refresh_from_db()
        html = self._render(self.message)
        self.assertIn('<button', html)
        self.assertIn('Formal', html)
        self.assertIn('Casual', html)
        self.assertIn('answer(', html)
        self.assertIn('x-data="messageInteraction()"', html)
        self.assertNotIn('pointer-events-none', html)
        self.assertNotIn('btn-primary', html)

    def test_answered_state_renders_non_clickable_with_highlight(self):
        interaction = MessageInteraction.objects.create(
            message=self.message,
            kind=MessageInteraction.Kind.QUESTION,
            payload={'question': 'Q', 'options': ['Formal', 'Casual']},
        )
        interaction.interacted_at = timezone.now()
        interaction.interacted_by = self.user
        interaction.state = {'selected_index': 0, 'answer_message_id': 'abc'}
        interaction.save()
        self.message.refresh_from_db()
        html = self._render(self.message)
        self.assertNotIn('<button', html)
        self.assertIn('pointer-events-none', html)
        self.assertIn('btn-primary', html)
        self.assertIn('opacity-40', html)
        self.assertIn('Formal', html)
        self.assertIn('Casual', html)

    def test_group_shows_answered_by(self):
        group = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.user,
        )
        ConversationMember.objects.create(conversation=group, user=self.user)
        ConversationMember.objects.create(conversation=group, user=self.bot)
        msg = Message.objects.create(
            conversation=group, author=self.bot, body='Q?',
        )
        interaction = MessageInteraction.objects.create(
            message=msg,
            kind=MessageInteraction.Kind.QUESTION,
            payload={'question': 'Q', 'options': ['A', 'B']},
        )
        interaction.interacted_at = timezone.now()
        interaction.interacted_by = self.user
        interaction.state = {'selected_index': 0, 'answer_message_id': 'abc'}
        interaction.save()
        msg.refresh_from_db()
        html = self._render(msg)
        self.assertIn('Répondu par', html)

    def test_dm_hides_answered_by(self):
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
        html = self._render(self.message)
        self.assertNotIn('Répondu par', html)
