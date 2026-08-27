"""Deleting a message must take its dependent rows and blobs with it.

The Message row itself is a tombstone and survives: the placeholder, the
reply quotes and the thread structure all read from it. Everything a member
could still reach *through* it must not.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

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
    ThreadParticipant,
)
from workspace.chat.services.reactions import quick_reactions_for

User = get_user_model()


class MessageDeletePurgeTests(APITestCase):
    def setUp(self):
        self.author = User.objects.create_user(username="author", password="pass")
        self.member = User.objects.create_user(username="member", password="pass")

        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title="Group",
            created_by=self.author,
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.author
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.member
        )

        self.message = Message.objects.create(
            conversation=self.conversation,
            author=self.author,
            body="hi",
        )
        self.client.force_authenticate(self.author)

    def tearDown(self):
        cache.clear()

    def url(self, message=None):
        message = message or self.message
        return (
            f"/api/v1/chat/conversations/{self.conversation.uuid}"
            f"/messages/{message.uuid}"
        )

    def _attach(self, name="voice.webm", content=b"webm-bytes"):
        return MessageAttachment.objects.create(
            message=self.message,
            file=SimpleUploadedFile(name, content, content_type="video/webm"),
            original_name=name,
            mime_type="video/webm",
            viewer="audio",
            size=len(content),
        )

    def _delete(self):
        """DELETE the message, running the post-commit storage cleanup."""
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.delete(self.url())

    def test_delete_removes_attachment_rows(self):
        self._attach()

        response = self._delete()

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            MessageAttachment.objects.filter(message=self.message).exists()
        )

    def test_delete_removes_attachment_blobs_from_storage(self):
        attachment = self._attach()
        path = attachment.file.name
        self.assertTrue(default_storage.exists(path))

        self._delete()

        self.assertFalse(default_storage.exists(path))

    def test_deleted_attachment_is_no_longer_downloadable(self):
        attachment = self._attach()
        # Warm the 60s metadata memo so the purge has to invalidate it, not
        # merely outlive it.
        warm = self.client.get(f"/api/v1/chat/attachments/{attachment.uuid}")
        b"".join(warm.streaming_content)

        self._delete()

        response = self.client.get(f"/api/v1/chat/attachments/{attachment.uuid}")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_removes_reactions(self):
        Reaction.objects.create(message=self.message, user=self.member, emoji="👍")

        self._delete()

        self.assertFalse(Reaction.objects.filter(message=self.message).exists())

    def test_delete_removes_link_previews(self):
        preview = LinkPreview.objects.create(url="https://example.com", title="Ex")
        MessageLinkPreview.objects.create(message=self.message, preview=preview)

        self._delete()

        self.assertFalse(
            MessageLinkPreview.objects.filter(message=self.message).exists()
        )
        # The preview itself is a shared per-URL cache, not message data.
        self.assertTrue(LinkPreview.objects.filter(pk=preview.pk).exists())

    def test_delete_removes_the_interaction_widget(self):
        MessageInteraction.objects.create(
            message=self.message,
            kind=MessageInteraction.Kind.QUESTION,
            payload={"suggestions": ["yes", "no"]},
        )

        self._delete()

        self.assertFalse(
            MessageInteraction.objects.filter(message=self.message).exists()
        )

    def test_delete_removes_pins(self):
        PinnedMessage.objects.create(
            conversation=self.conversation,
            message=self.message,
            pinned_by=self.author,
        )

        self._delete()

        self.assertFalse(PinnedMessage.objects.filter(message=self.message).exists())

    def test_delete_keeps_the_tombstone_row(self):
        self._attach()

        self._delete()

        self.message.refresh_from_db()
        self.assertIsNotNone(self.message.deleted_at)

    def test_delete_keeps_thread_read_state(self):
        """A deleted root keeps its replies, so it must keep their read state."""
        reply = Message.objects.create(
            conversation=self.conversation,
            author=self.member,
            body="reply",
            thread_root=self.message,
        )
        ThreadParticipant.objects.create(root_message=self.message, user=self.member)

        self._delete()

        self.assertTrue(
            ThreadParticipant.objects.filter(root_message=self.message).exists()
        )
        reply.refresh_from_db()
        self.assertEqual(reply.thread_root_id, self.message.uuid)

    def test_delete_refreshes_the_reactors_quick_reaction_bar(self):
        """The bar ranks an emoji the purge is about to take away."""
        Reaction.objects.create(message=self.message, user=self.member, emoji="🦀")
        self.assertIn("🦀", quick_reactions_for(self.member))

        self._delete()

        self.assertNotIn("🦀", quick_reactions_for(self.member))

    def test_deleted_attachment_is_unreachable_even_if_the_blob_survives(self):
        """The database is the authority, not the disk.

        A storage backend that refuses the delete (or acknowledges it late)
        must not keep the attachment served: the row is gone, so the memo the
        download view reads has to be invalidated on its own account.
        """
        attachment = self._attach()
        warm = self.client.get(f"/api/v1/chat/attachments/{attachment.uuid}")
        b"".join(warm.streaming_content)

        with patch(
            "django.db.models.fields.files.FieldFile.delete", side_effect=OSError
        ):
            self._delete()

        self.assertTrue(default_storage.exists(attachment.file.name))
        response = self.client.get(f"/api/v1/chat/attachments/{attachment.uuid}")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
