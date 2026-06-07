"""Regression test for MailAttachmentDownloadView.

Pins down that a vanished blob (storage cleanup, migration, manual deletion)
returns 404 instead of letting FileNotFoundError propagate as a 500. Mirrors
the behavior of MailAttachmentSaveToFilesView (test_attachment_save_to_files).
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
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


class MailAttachmentDownloadTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u", password="p")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@example.com",
            imap_host="imap.example.com",
            imap_use_ssl=True,
            smtp_host="smtp.example.com",
            username="user@example.com",
        )
        self.folder = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )
        self.message = MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            imap_uid=1,
            subject="hi",
        )
        self.attachment = MailAttachment.objects.create(
            message=self.message,
            filename="doc.pdf",
            content_type="application/pdf",
            size=3,
            content=SimpleUploadedFile(
                "doc.pdf",
                b"pdf",
                content_type="application/pdf",
            ),
        )
        self.url = f"/api/v1/mail/attachments/{self.attachment.uuid}"

    def test_download_succeeds(self):
        """Sanity: a present attachment downloads with status 200."""
        self.client.force_authenticate(self.user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp["Accept-Ranges"], "bytes")
        if hasattr(resp, "streaming_content"):
            b"".join(resp.streaming_content)

    def test_range_request_supports_resume(self):
        """Download managers issue Range to resume an interrupted save."""
        self.client.force_authenticate(self.user)
        resp = self.client.get(self.url, HTTP_RANGE="bytes=1-")
        self.assertEqual(resp.status_code, status.HTTP_206_PARTIAL_CONTENT)
        self.assertEqual(resp["Content-Range"], "bytes 1-2/3")
        body = b"".join(resp.streaming_content)
        self.assertEqual(body, b"df")

    def test_missing_blob_returns_404(self):
        """If the underlying blob has gone missing, attachment.content.open('rb')
        raises FileNotFoundError. Without the catch, this surfaces as 500.
        Must return 404 to mirror MailAttachmentSaveToFilesView."""
        self.client.force_authenticate(self.user)
        with patch.object(
            default_storage,
            "open",
            side_effect=FileNotFoundError("gone"),
        ):
            resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
