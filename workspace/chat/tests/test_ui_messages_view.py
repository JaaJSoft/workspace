from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    MeetingGuest,
    Message,
    MessageAttachment,
)
from workspace.chat.services.meetings import create_meeting

from .meeting_fixtures import make_event

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
        # Attachments reach the page as the JSON payload the
        # <chat-message-group> shell turns into the media mosaic and file
        # chips (including the data-attachment-* attributes the viewer's
        # prev/next navigation walks) - so assert every attachment is in the
        # payload, sorted into the right bucket.
        self._attach("a.png", "image/png", "image")
        self._attach("b.png", "image/png", "image")
        self._attach("c.mp4", "video/mp4", "video")
        self._attach("d.pdf", "application/pdf", "document")
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        for att in self.message.media_attachments:
            self.assertIn(
                f'{{"uuid": "{att.uuid}", "name": "{att.original_name}"', html
            )
        for att in self.message.file_attachments:
            self.assertIn(
                f'{{"uuid": "{att.uuid}", "name": "{att.original_name}"', html
            )
        self.assertEqual(len(self.message.media_attachments), 3)
        self.assertEqual(len(self.message.file_attachments), 1)

    def test_single_image_renders(self):
        att = self._attach("solo.png", "image/png", "image")
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn(f'"uuid": "{att.uuid}"', html)
        self.assertIn('"is_image": true', html)


class ConversationMessagesGuestAuthorTests(TestCase):
    """A meeting guest has no user row, so the group header must omit
    author-id rather than render the literal string "None" - the avatar
    element would then request /api/v1/users/None/avatar."""

    def setUp(self):
        self.user = User.objects.create_user(username="hosty", password="pw")
        event = make_event(self.user)
        self.meeting = create_meeting(event, self.user)
        self.guest = MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Ada",
            state=MeetingGuest.State.ADMITTED,
            occurrence_start=timezone.now(),
            token_hash="c" * 64,
        )
        Message.objects.create(
            conversation=self.meeting.conversation,
            author=self.user,
            body="member says hi",
        )
        Message.objects.create(
            conversation=self.meeting.conversation,
            guest=self.guest,
            body="guest says hi",
        )
        self.url = reverse(
            "chat_ui:conversation_messages",
            kwargs={"conversation_uuid": self.meeting.conversation_id},
        )
        self.client.force_login(self.user)

    def test_a_guest_group_carries_no_author_id(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertNotIn('author-id="None"', html)
        self.assertIn(f'author-id="{self.user.id}"', html)
