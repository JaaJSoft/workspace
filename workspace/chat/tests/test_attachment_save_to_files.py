from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    MessageAttachment,
)

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
