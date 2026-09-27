"""Regression test: the Sent copy of a message must keep its Bcc recipients.

The bytes handed to SMTP must never carry a Bcc header - the hidden
recipients would leak to everyone on the message. The copy APPENDed to the
Sent folder is a different matter: only the account owner can read it, and
the header is the sole place the Bcc list survives the IMAP round-trip
(imap_parse reads 'Bcc' into bcc_addresses). Archiving the outgoing bytes
verbatim left the user with no record of who was blind-copied.

Both halves are asserted together on purpose: dropping either one makes the
fix reversible without a test failing.
"""

from email import message_from_bytes
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from workspace.mail.models import MailAccount, MailFolder
from workspace.mail.services.smtp import send_email
from workspace.mail.tests.smtp_recorder import RecordingSMTP

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
        server = RecordingSMTP()
        mock_connect.return_value = server
        appended = []
        mock_append.side_effect = lambda account, file, msg_id: appended.append(
            file.read()
        )

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

        self.assertEqual(server.transactions, 1)
        self.assertIsNone(
            server.message["Bcc"],
            "the bytes given to SMTP must not expose the Bcc recipients",
        )
        self.assertEqual(
            sorted(server.recipients),
            ["bob@example.com", "dave@example.com", "eve@example.com"],
            "Bcc recipients still belong in the SMTP envelope",
        )

        self.assertEqual(len(appended), 1)
        archived = message_from_bytes(appended[0])
        self.assertEqual(
            archived["Bcc"],
            "dave@example.com, eve@example.com",
            "the Sent copy must record who was blind-copied",
        )

    @patch("workspace.mail.services.smtp.connect_smtp")
    def test_both_variants_share_message_id_and_attachments(self, mock_connect):
        mock_connect.return_value = RecordingSMTP()
        attachment = MagicMock()
        attachment.name = "doc.pdf"
        attachment.read.side_effect = [b"%PDF-fake", b""]

        with send_email(
            self.account,
            to=["bob@example.com"],
            subject="Sent with attachment",
            body_text="hi",
            bcc=["dave@example.com"],
            attachments=[attachment],
        ) as sent:
            outgoing_bytes = sent.outgoing().read()
            archived_bytes = sent.archived().read()

        outgoing = message_from_bytes(outgoing_bytes)
        archived = message_from_bytes(archived_bytes)
        self.assertEqual(
            outgoing["Message-ID"],
            archived["Message-ID"],
            "a divergent Message-ID would detach the archived copy from its thread",
        )
        self.assertEqual(sent.message_id, outgoing["Message-ID"])
        self.assertIn(b"doc.pdf", outgoing_bytes)
        self.assertIn(b"doc.pdf", archived_bytes)
        self.assertTrue(archived_bytes.endswith(outgoing_bytes))

    @patch("workspace.mail.services.smtp.connect_smtp")
    def test_no_bcc_header_when_no_bcc_recipients(self, mock_connect):
        mock_connect.return_value = RecordingSMTP()

        with send_email(
            self.account,
            to=["bob@example.com"],
            subject="No bcc",
            body_text="hi",
        ) as sent:
            outgoing_bytes = sent.outgoing().read()
            archived_bytes = sent.archived().read()

        self.assertIsNone(message_from_bytes(archived_bytes)["Bcc"])
        self.assertEqual(outgoing_bytes, archived_bytes)
