import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    MessageAttachment,
    PinnedMessage,
    Reaction,
    ThreadParticipant,
)
from workspace.files.models import File

User = get_user_model()


class ForeignRowsAnswerLikeAbsentOnesTests(TestCase):
    """A message or attachment of a conversation the caller is not in gets the
    answer an unknown uuid gets, so no endpoint confirms that it exists."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="secret")
        self.mallory = User.objects.create_user(username="mallory", password="secret")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.alice
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.alice
        )
        self.message = Message.objects.create(
            conversation=self.conversation, author=self.alice, body="private"
        )
        ThreadParticipant.objects.create(
            root_message=self.message, user=self.mallory, unread_count=2
        )
        self.attachment = MessageAttachment.objects.create(
            message=self.message,
            file=SimpleUploadedFile("doc.pdf", b"pdf", content_type="application/pdf"),
            original_name="doc.pdf",
            mime_type="application/pdf",
            size=3,
        )
        self.client.force_login(self.mallory)

    def tearDown(self):
        cache.clear()

    def _answers(self, method, url_name, kwarg, foreign_uuid, **kwargs):
        call = getattr(self.client, method)
        foreign = call(reverse(url_name, kwargs={kwarg: foreign_uuid}), **kwargs)
        absent = call(reverse(url_name, kwargs={kwarg: uuid.uuid4()}), **kwargs)
        return (foreign.status_code, foreign.content), (
            absent.status_code,
            absent.content,
        )

    def test_toggling_a_reaction(self):
        foreign, absent = self._answers(
            "post",
            "chat-reaction-toggle",
            "message_id",
            self.message.uuid,
            data={"emoji": "👍"},
            content_type="application/json",
        )

        self.assertEqual(foreign, absent)
        self.assertFalse(Reaction.objects.filter(message=self.message).exists())

    def test_pinning_a_message(self):
        foreign, absent = self._answers(
            "post", "chat-message-pin-toggle", "message_id", self.message.uuid
        )

        self.assertEqual(foreign, absent)
        self.assertFalse(PinnedMessage.objects.filter(message=self.message).exists())

    def test_unpinning_a_message(self):
        PinnedMessage.objects.create(
            conversation=self.conversation, message=self.message, pinned_by=self.alice
        )

        foreign, absent = self._answers(
            "delete", "chat-message-pin-toggle", "message_id", self.message.uuid
        )

        self.assertEqual(foreign, absent)
        self.assertTrue(PinnedMessage.objects.filter(message=self.message).exists())

    def test_saving_an_attachment_to_files(self):
        foreign, absent = self._answers(
            "post",
            "chat-attachment-save-to-files",
            "attachment_id",
            self.attachment.uuid,
        )

        self.assertEqual(foreign, absent)
        self.assertFalse(File.objects.filter(owner=self.mallory).exists())

    def test_marking_a_thread_read(self):
        foreign, absent = self._answers(
            "post", "chat-thread-read", "root_uuid", self.message.uuid
        )

        self.assertEqual(foreign, absent)
        participant = ThreadParticipant.objects.get(
            root_message=self.message, user=self.mallory
        )
        self.assertEqual(participant.unread_count, 2)
