from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.ai.models import AITask
from workspace.ai.services.responses import post_bot_message
from workspace.chat.models import (
    Conversation, ConversationMember, MessageInteraction,
)

User = get_user_model()


class PostBotMessageInteractionTests(TestCase):
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
        self.ai_task = AITask.objects.create(
            owner=self.user, task_type=AITask.TaskType.CHAT,
        )
        self.result = {
            'content': 'Quel ton ?', 'model': 'test',
            'prompt_tokens': 1, 'completion_tokens': 1,
        }

    def test_creates_interaction_when_context_has_question(self):
        tool_context = {
            'question': {
                'question': 'Quel ton ?',
                'options': ['Formal', 'Casual'],
            },
        }
        body, msg = post_bot_message(
            conversation=self.conv, bot_user=self.bot,
            result=self.result, used_tools=[], tool_context=tool_context,
            ai_task=self.ai_task,
        )
        interaction = MessageInteraction.objects.get(message=msg)
        self.assertEqual(interaction.kind, MessageInteraction.Kind.QUESTION)
        self.assertEqual(interaction.payload['question'], 'Quel ton ?')
        self.assertEqual(interaction.payload['options'], ['Formal', 'Casual'])
        self.assertIsNone(interaction.interacted_at)

    def test_no_interaction_when_context_missing_question(self):
        body, msg = post_bot_message(
            conversation=self.conv, bot_user=self.bot,
            result=self.result, used_tools=[], tool_context={},
            ai_task=self.ai_task,
        )
        self.assertFalse(MessageInteraction.objects.filter(message=msg).exists())
