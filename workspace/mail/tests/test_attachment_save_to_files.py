from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.mail.models import (
    MailAccount,
    MailAttachment,
    MailFolder,
    MailMessage,
)

User = get_user_model()


class MailAttachmentSaveToFilesTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='u', password='p')
        self.account = MailAccount.objects.create(
            owner=self.user,
            email='user@example.com',
            imap_host='imap.example.com',
            imap_use_ssl=True,
            smtp_host='smtp.example.com',
            username='user@example.com',
        )
        self.folder = MailFolder.objects.create(
            account=self.account,
            name='INBOX',
            display_name='Inbox',
            folder_type='inbox',
        )
        self.message = MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            imap_uid=1,
            subject='hi',
        )
        self.attachment = MailAttachment.objects.create(
            message=self.message,
            filename='doc.pdf',
            content_type='application/pdf',
            size=3,
            content=SimpleUploadedFile(
                'doc.pdf', b'pdf', content_type='application/pdf',
            ),
        )
        self.url = f'/api/v1/mail/attachments/{self.attachment.uuid}/save-to-files'

    def test_malformed_folder_id_returns_400(self):
        """Regression: folder_id was passed straight to File.objects.get(uuid=...)
        without validation. A non-UUID string raised ValidationError from the
        UUIDField cleaning layer, slipped past `except File.DoesNotExist:`, and
        surfaced as 500. Must validate and return 4xx.
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
