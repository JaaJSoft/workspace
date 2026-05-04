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
        self.user = User.objects.create_user(username='u', password='p')
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title='G',
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        self.message = Message.objects.create(
            conversation=self.conv, author=self.user, body='hi',
        )
        self.attachment = MessageAttachment.objects.create(
            message=self.message,
            file=SimpleUploadedFile(
                'doc.pdf', b'pdf', content_type='application/pdf',
            ),
            original_name='doc.pdf',
            mime_type='application/pdf',
            size=3,
        )
        self.url = (
            f'/api/v1/chat/attachments/{self.attachment.uuid}/save-to-files'
        )

    def test_malformed_folder_id_returns_400(self):
        """Regression: folder_id was passed straight to File.objects.get(uuid=...)
        without validation. A non-UUID string raised ValueError from the UUIDField
        cleaning layer, slipped past `except File.DoesNotExist:`, and surfaced as
        500. Must validate and return 4xx.
        """
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            self.url, data={'folder_id': 'not-a-uuid'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_save_to_root_succeeds(self):
        """Sanity: omitting folder_id saves the attachment at the root."""
        self.client.force_authenticate(self.user)
        resp = self.client.post(self.url, data={}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn('file_uuid', resp.data)

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
        resp = self.client.post(self.url, data={}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        saved = File.objects.get(uuid=resp.data['file_uuid'])
        self.assertNotEqual(saved.content.name, self.attachment.file.name)
        with saved.content.open('rb') as f:
            self.assertEqual(f.read(), b'pdf')

    def test_missing_blob_returns_404(self):
        """Regression: if the underlying attachment blob has gone missing,
        attachment.file.open('rb') raises FileNotFoundError/OSError. This
        used to escape the existing `except MessageAttachment.DoesNotExist:`
        and surface as 500. Must mirror the download view's 404 handling.
        """
        self.client.force_authenticate(self.user)
        with patch.object(
            default_storage, 'open', side_effect=FileNotFoundError('gone'),
        ):
            resp = self.client.post(self.url, data={}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
