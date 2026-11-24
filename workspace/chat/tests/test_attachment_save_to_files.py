from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    MessageAttachment,
)
from workspace.files.models import File

User = get_user_model()


class AttachmentSaveToFilesTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u", password="p")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title="G",
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
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
        self.url = f"/api/v1/chat/attachments/{self.attachment.uuid}/save-to-files"

    def test_malformed_folder_id_returns_400(self):
        """Regression: folder_id was passed straight to File.objects.get(uuid=...)
        without validation. A non-UUID string raised ValueError from the UUIDField
        cleaning layer, slipped past `except File.DoesNotExist:`, and surfaced as
        500. Must validate and return 4xx.
        """
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            self.url,
            data={"folder_id": "not-a-uuid"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_save_to_root_succeeds(self):
        """Sanity: omitting folder_id saves the attachment at the root."""
        self.client.force_authenticate(self.user)
        resp = self.client.post(self.url, data={}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn("file_uuid", resp.data)

    def test_save_creates_independent_copy(self):
        """The saved file must be a real copy of the attachment blob, not a
        shared reference. Specifically, the destination File's storage path
        must differ from the source attachment's path - otherwise deleting
        the conversation later would orphan the saved file. This pins down
        the FieldFile-vs-File _committed pitfall: if we ever pass the source
        FieldFile straight through to FileField, Django would skip
        storage.save() and the two rows would point at the same blob.
        """
        self.client.force_authenticate(self.user)
        resp = self.client.post(self.url, data={}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        saved = File.objects.get(uuid=resp.data["file_uuid"])
        self.assertNotEqual(saved.content.name, self.attachment.file.name)
        with saved.content.open("rb") as f:
            self.assertEqual(f.read(), b"pdf")

    def test_missing_blob_returns_404(self):
        """Regression: if the underlying attachment blob has gone missing,
        attachment.file.open('rb') raises FileNotFoundError/OSError. This
        used to escape the existing `except MessageAttachment.DoesNotExist:`
        and surface as 500. Must mirror the download view's 404 handling.
        """
        self.client.force_authenticate(self.user)
        with patch.object(
            default_storage,
            "open",
            side_effect=FileNotFoundError("gone"),
        ):
            resp = self.client.post(self.url, data={}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class SaveToFilesTargetFolderTests(APITestCase):
    """The target folder is one the user may create files in, nothing else."""

    def setUp(self):
        from django.contrib.auth.models import Group

        from workspace.files.services import tree

        self.user = User.objects.create_user(username="u", password="p")
        self.other = User.objects.create_user(username="o", password="p")
        self.team = Group.objects.create(name="Team")
        self.user.groups.add(self.team)
        self.other.groups.add(self.team)
        self.group_root = tree.create_folder(self.other, "Team", group=self.team)
        conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="G", created_by=self.user
        )
        ConversationMember.objects.create(conversation=conv, user=self.user)
        attachment = MessageAttachment.objects.create(
            message=Message.objects.create(
                conversation=conv, author=self.user, body="hi"
            ),
            file=SimpleUploadedFile("doc.pdf", b"pdf"),
            original_name="doc.pdf",
            mime_type="application/pdf",
            size=3,
        )
        self.url = f"/api/v1/chat/attachments/{attachment.uuid}/save-to-files"
        self.client.force_authenticate(self.user)

    def _save(self, folder_uuid):
        return self.client.post(self.url, {"folder_id": str(folder_uuid)}, "json")

    def test_a_folder_of_a_group_the_user_left_answers_like_a_missing_one(self):
        from uuid import uuid4

        from workspace.files.services import tree

        own_folder = tree.create_folder(self.user, "Mine", parent=self.group_root)
        self.user.groups.remove(self.team)

        left = self._save(own_folder.uuid)
        missing = self._save(uuid4())

        self.assertEqual(
            (left.status_code, left.content), (missing.status_code, missing.content)
        )
        self.assertEqual(left.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(File.objects.filter(parent=own_folder).exists())

    def test_a_folder_of_the_user_group_is_a_valid_target(self):
        response = self._save(self.group_root.uuid)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        saved = File.objects.get(uuid=response.data["file_uuid"])
        self.assertEqual(saved.parent_id, self.group_root.pk)
        self.assertEqual(saved.group_id, self.team.pk)

    def test_a_folder_shared_read_write_is_not_a_target(self):
        from workspace.files.models import FileShare
        from workspace.files.services import tree

        theirs = tree.create_folder(self.other, "Theirs")
        FileShare.objects.create(
            file=theirs,
            shared_by=self.other,
            shared_with=self.user,
            permission=FileShare.Permission.READ_WRITE,
        )

        response = self._save(theirs.uuid)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(File.objects.filter(parent=theirs).exists())
