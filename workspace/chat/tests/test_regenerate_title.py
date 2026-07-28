"""Tests for POST /api/v1/chat/conversations/<uuid>/regenerate-title."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.ai.models import BotProfile
from workspace.chat.models import Conversation, ConversationMember, Message

User = get_user_model()


class ConversationRegenerateTitleViewTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="user", password="pass123")
        self.other = User.objects.create_user(username="other", password="pass123")
        self.bot_user = User.objects.create_user(username="bot", password="pass123")
        BotProfile.objects.create(user=self.bot_user, system_prompt="Test bot.")

        self.bot_conv = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
            title="Old title",
        )
        ConversationMember.objects.create(conversation=self.bot_conv, user=self.user)
        ConversationMember.objects.create(
            conversation=self.bot_conv, user=self.bot_user
        )
        Message.objects.create(
            conversation=self.bot_conv, author=self.user, body="Hello bot"
        )

        self.human_conv = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.human_conv, user=self.user)
        ConversationMember.objects.create(conversation=self.human_conv, user=self.other)
        Message.objects.create(
            conversation=self.human_conv, author=self.user, body="Hi"
        )

    def url(self, conversation):
        return f"/api/v1/chat/conversations/{conversation.uuid}/regenerate-title"

    def test_unauthenticated_returns_403(self):
        resp = self.client.post(self.url(self.bot_conv))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_member_returns_404(self):
        self.client.force_authenticate(self.other)
        resp = self.client.post(self.url(self.bot_conv))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_non_bot_conversation_returns_400(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(self.url(self.human_conv))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_conversation_without_messages_returns_400(self):
        Message.objects.filter(conversation=self.bot_conv).delete()
        self.client.force_authenticate(self.user)
        resp = self.client.post(self.url(self.bot_conv))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("workspace.ai.tasks.chat.generate_conversation_title.delay")
    def test_dispatches_forced_title_generation(self, mock_delay):
        self.client.force_authenticate(self.user)
        resp = self.client.post(self.url(self.bot_conv))
        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        mock_delay.assert_called_once_with(str(self.bot_conv.uuid), force=True)
