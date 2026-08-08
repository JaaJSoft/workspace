"""Regression tests: a reply sent or saved from the app must carry the
In-Reply-To and References headers of the message it answers.

Without them the recipient's client opens a new conversation for every
reply, and our own thread reconstruction (services/threads.get_thread,
which walks In-Reply-To upward) sees the message coming back from the
Sent sync as an orphan.

The parent is identified by its MailMessage UUID and the Message-ID is
read from the DB: a client-supplied header value would let a caller graft
a reply onto any thread, including another account's.
"""

from email import message_from_string
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from workspace.mail.models import MailAccount, MailFolder, MailMessage

User = get_user_model()


class ReplyThreadingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="threader", password="pass")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
        )
        self.account.set_password("secret")
        self.account.save()
        self.folder = MailFolder.objects.create(
            account=self.account, name="INBOX", folder_type="inbox"
        )
        self.parent = MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            imap_uid=1,
            message_id="<parent@example.com>",
            references="<root@example.com>",
            subject="Hello",
            date=timezone.now(),
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _send(self, **extra):
        payload = {
            "account_id": str(self.account.uuid),
            "to": ["bob@example.com"],
            "subject": "Re: Hello",
            "body_text": "sure",
            **extra,
        }
        with patch("workspace.mail.services.smtp.connect_smtp") as mock_connect:
            with patch("workspace.mail.services.imap_messages.append_to_sent"):
                self.client.post("/api/v1/mail/messages/send", payload, format="json")
        mock_server = mock_connect.return_value
        mock_server.sendmail.assert_called_once()
        return message_from_string(mock_server.sendmail.call_args[0][2])

    def test_sent_reply_carries_in_reply_to_and_references(self):
        msg = self._send(reply_message_id=str(self.parent.uuid))
        self.assertEqual(
            msg["In-Reply-To"],
            "<parent@example.com>",
            "a reply without In-Reply-To detaches from its thread everywhere",
        )
        self.assertEqual(msg["References"], "<root@example.com> <parent@example.com>")

    def test_sent_reply_threads_under_its_parent_in_our_own_view(self):
        """The Sent copy comes back through imap_parse; get_thread must then
        find the parent instead of treating the reply as a solo message."""
        from workspace.mail.services.imap_parse import _parse_message
        from workspace.mail.services.threads import get_thread

        raw = self._send(reply_message_id=str(self.parent.uuid)).as_string()
        sent_folder = MailFolder.objects.create(
            account=self.account, name="Sent", folder_type="sent"
        )
        reply = _parse_message(
            raw.encode("utf-8"), self.account, sent_folder, uid=2, flags_str=""
        )
        self.assertEqual(get_thread(reply), [self.parent, reply])

    def test_fresh_message_has_no_threading_headers(self):
        msg = self._send()
        self.assertIsNone(msg["In-Reply-To"])
        self.assertIsNone(msg["References"])

    def test_parent_from_another_account_is_ignored(self):
        other_user = User.objects.create_user(username="other", password="pass")
        other_account = MailAccount.objects.create(
            owner=other_user,
            email="other@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="other@example.com",
        )
        other_folder = MailFolder.objects.create(
            account=other_account, name="INBOX", folder_type="inbox"
        )
        foreign = MailMessage.objects.create(
            account=other_account,
            folder=other_folder,
            imap_uid=1,
            message_id="<confidential@example.com>",
            date=timezone.now(),
        )

        msg = self._send(reply_message_id=str(foreign.uuid))
        self.assertIsNone(msg["In-Reply-To"])

    @patch("workspace.mail.services.imap_messages.save_draft")
    def test_saved_draft_reply_carries_threading_headers(self, mock_save):
        mock_save.return_value = None

        self.client.post(
            "/api/v1/mail/drafts",
            {
                "account_id": str(self.account.uuid),
                "to": ["bob@example.com"],
                "subject": "Re: Hello",
                "body_text": "later",
                "reply_message_id": str(self.parent.uuid),
            },
            format="json",
        )

        mock_save.assert_called_once()
        msg = message_from_string(mock_save.call_args[0][1].decode("utf-8"))
        self.assertEqual(msg["In-Reply-To"], "<parent@example.com>")
        self.assertEqual(msg["References"], "<root@example.com> <parent@example.com>")
