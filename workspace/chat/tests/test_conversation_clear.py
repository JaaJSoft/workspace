from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.ai.models import BotProfile
from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    MessageAttachment,
)

User = get_user_model()


def _add_bot(conversation, username="bot"):
    bot_user = User.objects.create_user(username=username, password="p")
    BotProfile.objects.create(user=bot_user)
    ConversationMember.objects.create(conversation=conversation, user=bot_user)
    return bot_user


class ConversationClearLogSanitizationTests(APITestCase):
    """Tests for log-injection hardening in ConversationClearView's
    post-commit attachment cleanup."""

    def setUp(self):
        self.user = User.objects.create_user(username="u", password="p")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title="G",
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        _add_bot(self.conv)
        self.message = Message.objects.create(
            conversation=self.conv,
            author=self.user,
            body="hi",
        )
        self.attachment = MessageAttachment.objects.create(
            message=self.message,
            file=SimpleUploadedFile(
                "doc.pdf",
                b"pdf",
                content_type="application/pdf",
            ),
            original_name="doc.pdf",
            mime_type="application/pdf",
            size=3,
        )
        # Inject CR/LF into the stored path. Bypass Django's storage-side
        # sanitization by writing the column directly so the FieldFile loaded
        # by the view carries the taint into the log call site.
        self.malicious_name = "evil\r\nFAKE LOG LINE\r\nx.pdf"
        MessageAttachment.objects.filter(uuid=self.attachment.uuid).update(
            file=self.malicious_name,
        )

    def test_cleanup_log_strips_crlf_from_filename(self):
        """Regression: when storage cleanup fails on a chat attachment, the
        path was logged unsanitized. CR/LF in user-uploaded filenames could
        forge fake log lines (CWE-117 log injection). The path must pass
        through scrub() at the call site.
        """
        self.client.force_authenticate(self.user)

        with patch(
            "django.core.files.storage.default_storage.delete",
            side_effect=OSError("storage gone"),
        ):
            with self.assertLogs(
                "workspace.chat.services.deletion",
                level="WARNING",
            ) as cm:
                with self.captureOnCommitCallbacks(execute=True):
                    resp = self.client.delete(
                        f"/api/v1/chat/conversations/{self.conv.uuid}/clear",
                    )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(cm.records), 1)
        formatted = cm.records[0].getMessage()
        self.assertNotIn("\n", formatted)
        self.assertNotIn("\r", formatted)


class ConversationClearAttachmentCacheTests(APITestCase):
    """Clearing a conversation must make its attachments unreachable at once."""

    def setUp(self):
        self.user = User.objects.create_user(username="u", password="p")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title="G",
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        _add_bot(self.conv)
        self.message = Message.objects.create(
            conversation=self.conv,
            author=self.user,
            body="hi",
        )
        self.attachment = MessageAttachment.objects.create(
            message=self.message,
            file=SimpleUploadedFile("doc.pdf", b"pdf", content_type="application/pdf"),
            original_name="doc.pdf",
            mime_type="application/pdf",
            size=3,
        )
        self.client.force_authenticate(self.user)

    def tearDown(self):
        cache.clear()

    def test_cleared_attachment_is_unreachable_even_if_the_blob_survives(self):
        """The database is the authority, not the disk: the download view's
        metadata memo has to be invalidated on the row's disappearance alone,
        not merely on the blob turning up missing.
        """
        url = f"/api/v1/chat/attachments/{self.attachment.uuid}"
        warm = self.client.get(url)
        b"".join(warm.streaming_content)

        with patch(
            "django.core.files.storage.default_storage.delete",
            side_effect=OSError("storage gone"),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                resp = self.client.delete(
                    f"/api/v1/chat/conversations/{self.conv.uuid}/clear",
                )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(default_storage.exists(self.attachment.file.name))
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)


class ConversationClearPermissionTests(APITestCase):
    """Clearing hard-deletes every message, so only a private chat with a bot
    can be cleared: no other person loses their history."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="p")
        self.bob = User.objects.create_user(username="bob", password="p")

    def tearDown(self):
        cache.clear()

    def _conversation(self, kind, *humans, bot=False):
        conv = Conversation.objects.create(kind=kind, title="", created_by=humans[0])
        for user in humans:
            ConversationMember.objects.create(conversation=conv, user=user)
        if bot:
            _add_bot(conv, username=f"bot-{conv.uuid}")
        Message.objects.create(conversation=conv, author=humans[0], body="note")
        return conv

    def _clear(self, user, conv):
        self.client.force_authenticate(user)
        return self.client.delete(f"/api/v1/chat/conversations/{conv.uuid}/clear")

    def test_member_cannot_clear_a_group_shared_with_another_person(self):
        conv = self._conversation(
            Conversation.Kind.GROUP, self.alice, self.bob, bot=True
        )
        resp = self._clear(self.bob, conv)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Message.objects.filter(conversation=conv).exists())

    def test_member_cannot_clear_a_dm_with_another_person(self):
        conv = self._conversation(Conversation.Kind.DM, self.alice, self.bob)
        resp = self._clear(self.bob, conv)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Message.objects.filter(conversation=conv).exists())

    def test_conversation_without_a_bot_cannot_be_cleared(self):
        conv = self._conversation(Conversation.Kind.GROUP, self.alice)
        resp = self._clear(self.alice, conv)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_dm_with_a_bot_can_be_cleared(self):
        conv = self._conversation(Conversation.Kind.DM, self.alice, bot=True)
        resp = self._clear(self.alice, conv)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(Message.objects.filter(conversation=conv).exists())

    def test_people_who_left_do_not_count(self):
        conv = self._conversation(
            Conversation.Kind.GROUP, self.alice, self.bob, bot=True
        )
        ConversationMember.objects.filter(conversation=conv, user=self.bob).update(
            left_at=timezone.now()
        )
        resp = self._clear(self.alice, conv)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_conversation_list_flags_what_the_viewer_may_clear(self):
        bot_dm = self._conversation(Conversation.Kind.DM, self.alice, bot=True)
        shared = self._conversation(
            Conversation.Kind.GROUP, self.alice, self.bob, bot=True
        )
        self.client.force_authenticate(self.alice)
        resp = self.client.get("/api/v1/chat/conversations")
        can_clear = {c["uuid"]: c["can_clear"] for c in resp.data}
        self.assertEqual(can_clear, {str(bot_dm.uuid): True, str(shared.uuid): False})
