"""Attaching workspace files to an outgoing email via ``file_uuids``.

Safety net for the resolve-and-authorize block: accessible files are
streamed into the SMTP message, anything unknown or inaccessible rejects
the send as a whole.
"""

import uuid
from email import message_from_string
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.services import FileService
from workspace.mail.models import MailAccount

User = get_user_model()


@patch("workspace.mail.services.imap_sync.sync_folder_messages")
@patch("workspace.mail.services.imap_messages.append_to_sent")
@patch("workspace.mail.services.smtp.connect_smtp")
class SendPickedFilesTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sender", password="p")
        self.other = User.objects.create_user(username="other", password="p")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="sender@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="sender@example.com",
        )
        self.account.set_password("secret")
        self.account.save()
        self.client.force_authenticate(self.user)

    def _send(self, file_uuids):
        return self.client.post(
            "/api/v1/mail/messages/send",
            {
                "account_id": str(self.account.uuid),
                "to": ["bob@example.com"],
                "subject": "With attachment",
                "body_text": "hi",
                "file_uuids": [str(u) for u in file_uuids],
            },
            format="json",
        )

    def test_owned_file_is_attached_to_the_outgoing_message(
        self, mock_connect, _append, _sync
    ):
        server = MagicMock()
        mock_connect.return_value = server
        src = FileService.create_file(
            self.user,
            "report.txt",
            content=SimpleUploadedFile(
                "report.txt", b"quarterly numbers", content_type="text/plain"
            ),
        )
        resp = self._send([src.uuid])
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        server.sendmail.assert_called_once()
        outgoing = message_from_string(server.sendmail.call_args[0][2])
        parts = [p for p in outgoing.walk() if p.get_filename() == "report.txt"]
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].get_payload(decode=True), b"quarterly numbers")

    def test_foreign_file_rejects_the_send(self, mock_connect, _append, _sync):
        server = MagicMock()
        mock_connect.return_value = server
        src = FileService.create_file(
            self.other,
            "secret.txt",
            content=SimpleUploadedFile("secret.txt", b"x", content_type="text/plain"),
        )
        resp = self._send([src.uuid])
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        server.sendmail.assert_not_called()

    def test_unknown_uuid_rejects_the_send(self, mock_connect, _append, _sync):
        server = MagicMock()
        mock_connect.return_value = server
        resp = self._send([uuid.uuid4()])
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        server.sendmail.assert_not_called()
