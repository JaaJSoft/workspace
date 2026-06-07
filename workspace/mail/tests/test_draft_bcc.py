"""Regression test: saving a draft must persist its Bcc recipients.

build_draft_message omits the Bcc header by design on the send path (Bcc
recipients travel in the SMTP envelope only), but a draft's Bcc list has no
other home than the header: the draft is APPENDed to the IMAP Drafts folder
and re-parsed on open (imap_parse reads 'Bcc' into bcc_addresses). Without
the header, reopening a saved draft silently drops every Bcc recipient.
"""

from email import message_from_string
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from workspace.mail.models import MailAccount

User = get_user_model()


class DraftSaveBccTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="draftbcc", password="pass")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
        )
        self.account.set_password("secret")
        self.account.save()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch("workspace.mail.services.imap_messages.save_draft")
    def test_saved_draft_contains_bcc_header(self, mock_save):
        mock_save.return_value = None

        self.client.post(
            "/api/v1/mail/drafts",
            {
                "account_id": str(self.account.uuid),
                "to": ["bob@example.com"],
                "subject": "Draft with bcc",
                "body_text": "hi",
                "bcc": ["dave@example.com"],
            },
            format="json",
        )

        mock_save.assert_called_once()
        raw_msg = mock_save.call_args[0][1]
        msg = message_from_string(raw_msg.decode("utf-8"))
        self.assertEqual(
            msg["Bcc"],
            "dave@example.com",
            "Draft saved to IMAP must carry its Bcc recipients in the header, "
            "otherwise reopening the draft loses them",
        )
