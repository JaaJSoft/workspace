from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    MessageAttachment,
)

User = get_user_model()


class ConversationMessagesViewAttachmentTests(TestCase):
    """The messages partial must render every attachment of a message."""

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.user
        )
        self.message = Message.objects.create(
            conversation=self.conversation, author=self.user, body="see files"
        )
        self.url = reverse(
            "chat_ui:conversation_messages",
            kwargs={"conversation_uuid": self.conversation.uuid},
        )
        self.client.force_login(self.user)

    def _attach(self, name, mime, category):
        return MessageAttachment.objects.create(
            message=self.message,
            file=SimpleUploadedFile(name, b"x", content_type=mime),
            original_name=name,
            mime_type=mime,
            category=category,
            size=1,
        )

    def test_all_attachments_render(self):
        attachments = [
            self._attach("a.png", "image/png", "image"),
            self._attach("b.png", "image/png", "image"),
            self._attach("c.mp4", "video/mp4", "video"),
            self._attach("d.pdf", "application/pdf", "document"),
        ]
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        # Media embed a src URL; plain files only reference their uuid, so
        # assert on the uuid to cover both shapes.
        for att in attachments:
            self.assertIn(str(att.uuid), html)
        # The viewer's prev/next navigation walks these data attributes.
        for att in attachments:
            self.assertIn(f'data-attachment-uuid="{att.uuid}"', html)

    def test_single_image_renders(self):
        att = self._attach("solo.png", "image/png", "image")
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(f"/api/v1/chat/attachments/{att.uuid}", resp.content.decode())
