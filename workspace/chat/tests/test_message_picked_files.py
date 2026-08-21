"""Attaching workspace files to a chat message via ``file_uuids``.

Safety net for the resolve-and-authorize block: accessible files are
stream-copied into fresh ``MessageAttachment`` blobs, anything unknown,
deleted, foreign or non-file is rejected as a whole.
"""

import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.chat.models import Conversation, ConversationMember, MessageAttachment
from workspace.files.models import FileShare
from workspace.files.services import FileService

User = get_user_model()


@patch("workspace.chat.services.notifications.notify_sse")
@patch("workspace.notifications.tasks.send_push_notification.delay")
class MessagePickedFilesTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="author", password="p")
        self.other = User.objects.create_user(username="other", password="p")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title="G",
            created_by=self.user,
        )
        for u in (self.user, self.other):
            ConversationMember.objects.create(conversation=self.conv, user=u)
        self.url = f"/api/v1/chat/conversations/{self.conv.uuid}/messages"
        self.client.force_authenticate(self.user)

    def _make_file(self, owner, name="doc.txt", content=b"hello"):
        return FileService.create_file(
            owner,
            name,
            content=SimpleUploadedFile(name, content, content_type="text/plain"),
        )

    def _post(self, file_uuids, body=""):
        return self.client.post(
            self.url,
            data={"body": body, "file_uuids": [str(u) for u in file_uuids]},
            format="json",
        )

    def test_owned_file_is_copied_into_a_fresh_attachment(self, _push, _sse):
        src = self._make_file(self.user)
        resp = self._post([src.uuid])
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        attachment = MessageAttachment.objects.get()
        self.assertEqual(attachment.original_name, "doc.txt")
        # Fresh blob, not a shared storage path: deleting the source must
        # not orphan the attachment.
        self.assertNotEqual(attachment.file.name, src.content.name)
        with attachment.file.open("rb") as f:
            self.assertEqual(f.read(), b"hello")

    def test_file_shared_with_the_user_is_attachable(self, _push, _sse):
        src = self._make_file(self.other)
        FileShare.objects.create(
            file=src,
            shared_by=self.other,
            shared_with=self.user,
            permission=FileShare.Permission.READ_ONLY,
        )
        resp = self._post([src.uuid])
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(MessageAttachment.objects.count(), 1)

    def test_foreign_file_is_rejected(self, _push, _sse):
        src = self._make_file(self.other)
        resp = self._post([src.uuid])
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(MessageAttachment.objects.count(), 0)

    def test_unknown_uuid_is_rejected(self, _push, _sse):
        resp = self._post([uuid.uuid4()])
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_trashed_file_is_rejected(self, _push, _sse):
        from django.utils import timezone

        src = self._make_file(self.user)
        src.deleted_at = timezone.now()
        src.save(update_fields=["deleted_at"])
        resp = self._post([src.uuid])
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_folder_is_rejected(self, _push, _sse):
        folder = FileService.create_folder(self.user, "Stuff")
        resp = self._post([folder.uuid])
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_one_bad_uuid_rejects_the_whole_batch(self, _push, _sse):
        src = self._make_file(self.user)
        resp = self._post([src.uuid, uuid.uuid4()])
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(MessageAttachment.objects.count(), 0)
