"""Backfill for messages deleted before the purge existed.

Exercises the function migration 0029 runs, with the real models rather than
the historical ones. What it must not touch matters as much as what it clears:
the backlog is selected by ``deleted_at``, and a live message that happens to
sit next to one has to come out untouched.
"""

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    LinkPreview,
    Message,
    MessageAttachment,
    MessageInteraction,
    MessageLinkPreview,
    PinnedMessage,
    Reaction,
)
from workspace.chat.services.deletion import purge_deleted_message_backlog

User = get_user_model()


class PurgeDeletedMessageBacklogTests(TestCase):
    def setUp(self):
        self.author = User.objects.create_user(username="author", password="x")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title="Group",
            created_by=self.author,
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.author
        )

    def _message(self, body, *, deleted):
        return Message.objects.create(
            conversation=self.conversation,
            author=self.author,
            body=body,
            body_html=f"<p>{body}</p>",
            tool_data={"steps": [body]},
            deleted_at=timezone.now() if deleted else None,
        )

    def _attach(self, message, name="voice.webm"):
        return MessageAttachment.objects.create(
            message=message,
            file=SimpleUploadedFile(name, b"webm-bytes", content_type="video/webm"),
            original_name=name,
            mime_type="video/webm",
            viewer="audio",
            size=10,
        )

    def _purge(self):
        with self.captureOnCommitCallbacks(execute=True):
            purge_deleted_message_backlog(
                messages=Message,
                attachments=MessageAttachment,
                reactions=Reaction,
                link_previews=MessageLinkPreview,
                interactions=MessageInteraction,
                pins=PinnedMessage,
            )

    def test_it_blanks_the_content_of_already_deleted_messages(self):
        message = self._message("the secret", deleted=True)

        self._purge()

        message.refresh_from_db()
        self.assertEqual(message.body, "")
        self.assertEqual(message.body_html, "")
        self.assertIsNone(message.tool_data)

    def test_it_removes_their_attachment_rows_and_blobs(self):
        message = self._message("with a voice note", deleted=True)
        path = self._attach(message).file.name

        self._purge()

        self.assertFalse(MessageAttachment.objects.filter(message=message).exists())
        self.assertFalse(default_storage.exists(path))

    def test_it_removes_their_dependent_rows(self):
        message = self._message("noisy", deleted=True)
        Reaction.objects.create(message=message, user=self.author, emoji="👍")
        preview = LinkPreview.objects.create(url="https://example.com", title="Ex")
        MessageLinkPreview.objects.create(message=message, preview=preview)
        MessageInteraction.objects.create(
            message=message,
            kind=MessageInteraction.Kind.QUESTION,
            payload={"suggestions": ["yes"]},
        )
        PinnedMessage.objects.create(
            conversation=self.conversation, message=message, pinned_by=self.author
        )

        self._purge()

        self.assertFalse(Reaction.objects.filter(message=message).exists())
        self.assertFalse(MessageLinkPreview.objects.filter(message=message).exists())
        self.assertFalse(MessageInteraction.objects.filter(message=message).exists())
        self.assertFalse(PinnedMessage.objects.filter(message=message).exists())
        self.assertTrue(LinkPreview.objects.filter(pk=preview.pk).exists())

    def test_it_leaves_live_messages_alone(self):
        live = self._message("still here", deleted=False)
        path = self._attach(live, name="kept.webm").file.name
        Reaction.objects.create(message=live, user=self.author, emoji="👍")
        self._message("gone", deleted=True)

        self._purge()

        live.refresh_from_db()
        self.assertEqual(live.body, "still here")
        self.assertEqual(live.body_html, "<p>still here</p>")
        self.assertEqual(live.tool_data, {"steps": ["still here"]})
        self.assertTrue(MessageAttachment.objects.filter(message=live).exists())
        self.assertTrue(Reaction.objects.filter(message=live).exists())
        self.assertTrue(default_storage.exists(path))

    def test_it_keeps_the_tombstone_rows(self):
        message = self._message("gone", deleted=True)

        self._purge()

        message.refresh_from_db()
        self.assertIsNotNone(message.deleted_at)
        self.assertEqual(message.author_id, self.author.id)

    def test_running_it_twice_is_safe(self):
        message = self._message("gone", deleted=True)
        self._attach(message)

        self._purge()
        self._purge()

        message.refresh_from_db()
        self.assertEqual(message.body, "")
        self.assertFalse(MessageAttachment.objects.filter(message=message).exists())
