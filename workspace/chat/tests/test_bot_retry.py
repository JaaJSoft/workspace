from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APITestCase

from workspace.ai.models import BotProfile
from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    MessageAttachment,
)

User = get_user_model()


@patch("workspace.chat.views.conversations._trigger_bot_response")
class BotRetryTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bot = User.objects.create_user(username="bot")
        BotProfile.objects.create(user=self.bot)
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.alice
        )
        self.alice_member = ConversationMember.objects.create(
            conversation=self.conv, user=self.alice
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.bot)
        Message.objects.create(
            conversation=self.conv,
            author=self.alice,
            body="hello",
        )
        self.failed = Message.objects.create(
            conversation=self.conv, author=self.bot, body="Something went wrong"
        )
        self.client.force_authenticate(self.alice)

    def retry(self):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse(
                    "chat-bot-retry",
                    kwargs={
                        "conversation_id": self.conv.uuid,
                        "message_id": self.failed.uuid,
                    },
                )
            )

    def test_the_retried_message_is_gone_and_the_bot_runs_again(self, trigger):
        resp = self.retry()

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Message.objects.filter(pk=self.failed.pk).exists())
        trigger.assert_called_once()

    def test_its_attachment_blobs_are_discarded(self, trigger):
        attachment = MessageAttachment.objects.create(
            message=self.failed,
            file=SimpleUploadedFile("out.txt", b"bot output"),
            original_name="out.txt",
            mime_type="text/plain",
            size=10,
        )
        name = attachment.file.name
        self.assertTrue(default_storage.exists(name))

        self.retry()

        self.assertFalse(default_storage.exists(name))

    def test_it_stops_counting_as_unread(self, trigger):
        ConversationMember.objects.filter(pk=self.alice_member.pk).update(
            unread_count=1
        )

        self.retry()

        self.alice_member.refresh_from_db()
        self.assertEqual(self.alice_member.unread_count, 0)

    def test_the_other_members_are_notified(self, trigger):
        with patch("workspace.chat.services.notifications.notify_sse") as notify:
            self.retry()

        notify.assert_any_call("chat", self.bot.pk, dirty=True)
