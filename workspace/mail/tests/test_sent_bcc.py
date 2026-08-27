"""Regression test: the Sent copy of a message must keep its Bcc recipients.

The bytes handed to sendmail must never carry a Bcc header - the hidden
recipients would leak to everyone on the message. The copy APPENDed to the
Sent folder is a different matter: only the account owner can read it, and
the header is the sole place the Bcc list survives the IMAP round-trip
(imap_parse reads 'Bcc' into bcc_addresses). Archiving the outgoing bytes
verbatim left the user with no record of who was blind-copied.

Both halves are asserted together on purpose: dropping either one makes the
fix reversible without a test failing.
"""

from email import message_from_string
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from workspace.mail.models import MailAccount, MailFolder
from workspace.mail.services.smtp import send_email

User = get_user_model()


class SendBccArchivalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sentbcc", password="pass")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
        )
        self.account.set_password("secret")
        self.account.save()
        # The archival half only runs on an account that has somewhere to
        # file the copy; without this the append is skipped and the bytes
        # asserted below are never produced.
        MailFolder.objects.create(
            account=self.account,
            name="Sent",
            display_name="Sent",
            folder_type="sent",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch("workspace.mail.services.imap_sync.sync_folder_messages")
    @patch("workspace.mail.services.imap_messages.append_to_sent")
    @patch("workspace.mail.services.smtp.connect_smtp")
    def test_bcc_omitted_from_smtp_but_kept_in_sent_copy(
        self, mock_connect, mock_append, mock_sync
    ):
        server = MagicMock()
        mock_connect.return_value = server

        resp = self.client.post(
            "/api/v1/mail/messages/send",
            {
                "account_id": str(self.account.uuid),
                "to": ["bob@example.com"],
                "subject": "Sent with bcc",
                "body_text": "hi",
                "bcc": ["dave@example.com", "eve@example.com"],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)

        server.sendmail.assert_called_once()
        outgoing = message_from_string(server.sendmail.call_args[0][2])
        self.assertIsNone(
            outgoing["Bcc"],
            "the bytes given to sendmail must not expose the Bcc recipients",
        )
        self.assertEqual(
            sorted(server.sendmail.call_args[0][1]),
            ["bob@example.com", "dave@example.com", "eve@example.com"],
            "Bcc recipients still belong in the SMTP envelope",
        )

        mock_append.assert_called_once()
        archived = message_from_string(mock_append.call_args[0][1].decode("utf-8"))
        self.assertEqual(
            archived["Bcc"],
            "dave@example.com, eve@example.com",
            "the Sent copy must record who was blind-copied",
        )

    @patch("workspace.mail.services.smtp.connect_smtp")
    def test_both_variants_share_message_id_and_attachments(self, mock_connect):
        mock_connect.return_value = MagicMock()
        attachment = MagicMock()
        attachment.name = "doc.pdf"
        attachment.read.return_value = b"%PDF-fake"

        sent = send_email(
            self.account,
            to=["bob@example.com"],
            subject="Sent with attachment",
            body_text="hi",
            bcc=["dave@example.com"],
            attachments=[attachment],
        )

        outgoing = message_from_string(sent.outgoing.decode("utf-8"))
        archived = message_from_string(sent.archived.decode("utf-8"))
        self.assertEqual(
            outgoing["Message-ID"],
            archived["Message-ID"],
            "a divergent Message-ID would detach the archived copy from its thread",
        )
        attachment.read.assert_called_once_with()
        self.assertIn(b"doc.pdf", sent.outgoing)
        self.assertIn(b"doc.pdf", sent.archived)

    @patch("workspace.mail.services.smtp.connect_smtp")
    def test_no_bcc_header_when_no_bcc_recipients(self, mock_connect):
        mock_connect.return_value = MagicMock()

        sent = send_email(
            self.account,
            to=["bob@example.com"],
            subject="No bcc",
            body_text="hi",
        )

        self.assertIsNone(message_from_string(sent.archived.decode("utf-8"))["Bcc"])
        self.assertEqual(sent.outgoing, sent.archived)
