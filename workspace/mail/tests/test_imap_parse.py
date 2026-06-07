"""Tests for _parse_message in workspace.mail.services.imap_parse."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder
from workspace.mail.services.imap_parse import _parse_message

User = get_user_model()


class ImapParseTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="parsertestuser",
            email="parser@test.com",
            password="pass123",
        )
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="parser@example.com",
            imap_host="imap.example.com",
            imap_use_ssl=True,
            smtp_host="smtp.example.com",
            username="parser@example.com",
        )
        self.account.set_password("secret")
        self.account.save()
        self.folder = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )

    def test_in_reply_to_header_is_captured(self):
        """A raw email with In-Reply-To: <parent@example.com> must persist
        that value to MailMessage.in_reply_to."""
        raw = (
            b"From: alice@example.com\r\n"
            b"To: bob@example.com\r\n"
            b"Subject: Re: Coffee?\r\n"
            b"Message-ID: <child@example.com>\r\n"
            b"In-Reply-To: <parent@example.com>\r\n"
            b"Date: Thu, 14 May 2026 10:00:00 +0000\r\n"
            b"\r\n"
            b"Sure, 3pm works.\r\n"
        )
        msg = _parse_message(raw, self.account, self.folder, uid=42, flags_str="")
        self.assertEqual(msg.in_reply_to, "<parent@example.com>")

    def test_in_reply_to_absent_defaults_to_empty(self):
        raw = (
            b"From: alice@example.com\r\n"
            b"Message-ID: <solo@example.com>\r\n"
            b"Date: Thu, 14 May 2026 10:00:00 +0000\r\n"
            b"\r\n"
            b"Body\r\n"
        )
        msg = _parse_message(raw, self.account, self.folder, uid=43, flags_str="")
        self.assertEqual(msg.in_reply_to, "")
