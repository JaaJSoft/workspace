"""Tests for _parse_message in workspace.mail.services.imap_parse."""

from datetime import UTC, datetime
from email.message import EmailMessage
from unittest.mock import patch

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
        msg = _parse_message(raw, self.account, self.folder, uid=42, flags=())
        self.assertEqual(msg.in_reply_to, "<parent@example.com>")

    def test_in_reply_to_absent_defaults_to_empty(self):
        raw = (
            b"From: alice@example.com\r\n"
            b"Message-ID: <solo@example.com>\r\n"
            b"Date: Thu, 14 May 2026 10:00:00 +0000\r\n"
            b"\r\n"
            b"Body\r\n"
        )
        msg = _parse_message(raw, self.account, self.folder, uid=43, flags=())
        self.assertEqual(msg.in_reply_to, "")

    def test_references_header_is_captured_unfolded(self):
        """References is routinely folded across lines; the stored chain must
        be a single space-separated list so a reply can extend it."""
        raw = (
            b"From: alice@example.com\r\n"
            b"Message-ID: <child@example.com>\r\n"
            b"References: <root@example.com>\r\n"
            b"\t<parent@example.com>\r\n"
            b"Date: Thu, 14 May 2026 10:00:00 +0000\r\n"
            b"\r\n"
            b"Body\r\n"
        )
        msg = _parse_message(raw, self.account, self.folder, uid=44, flags=())
        self.assertEqual(msg.references, "<root@example.com> <parent@example.com>")

    def test_references_absent_defaults_to_empty(self):
        raw = (
            b"From: alice@example.com\r\n"
            b"Message-ID: <solo2@example.com>\r\n"
            b"Date: Thu, 14 May 2026 10:00:00 +0000\r\n"
            b"\r\n"
            b"Body\r\n"
        )
        msg = _parse_message(raw, self.account, self.folder, uid=45, flags=())
        self.assertEqual(msg.references, "")


class HostileMessageTests(ImapParseTests):
    """Mail nobody proofread: every one of these has to be imported, since a
    message that fails to parse is one the user never sees."""

    def _parse(self, raw, uid=1):
        return _parse_message(raw, self.account, self.folder, uid=uid, flags=())

    def test_text_without_a_charset_is_read_as_utf8(self):
        raw = "From: cron@example.com\r\n\r\nSauvegarde termin\u00e9e\r\n".encode()

        self.assertEqual(self._parse(raw).body_text, "Sauvegarde termin\u00e9e\r\n")

    def test_bytes_that_decode_to_nothing_are_replaced(self):
        raw = (
            b"From: Zo\xe9 <zoe@example.com>\r\n"
            b"Subject: R\xe9union\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"\r\n"
            b"D\xe9cal\xe9e\r\n"
        )

        msg = self._parse(raw)

        self.assertEqual(msg.subject, "R\ufffdunion")
        self.assertEqual(msg.from_name, "Zo\ufffd")
        self.assertEqual(msg.from_email, "zoe@example.com")
        self.assertEqual(msg.body_text, "D\ufffdcal\ufffde\r\n")

    def test_nul_characters_are_dropped(self):
        raw = b"From: a@example.com\r\nSubject: a\x00b\r\n\r\nc\x00d\r\n"

        msg = self._parse(raw)

        self.assertEqual(msg.subject, "ab")
        self.assertEqual(msg.body_text, "cd\r\n")

    def test_a_sender_without_a_domain_is_kept_as_written(self):
        raw = b"From: Mailer Daemon\r\nTo: root, <>, a@example.com\r\n\r\nBounced\r\n"

        msg = self._parse(raw)

        self.assertEqual(msg.from_email, "Mailer Daemon")
        self.assertEqual(
            msg.to_addresses,
            [{"name": "", "email": "root"}, {"name": "", "email": "a@example.com"}],
        )

    def test_a_name_that_looks_like_an_address_does_not_hide_the_address(self):
        raw = b"From: user@example.com <user@example.com>\r\n\r\nHi\r\n"

        self.assertEqual(self._parse(raw).from_email, "user@example.com")

    def test_a_date_nothing_can_read_falls_back_to_now(self):
        before = datetime.now(UTC)
        for uid, date in enumerate(
            (b"garbage", b"Mon, 99 Foo 2026 25:61:00 +0000", b""), start=1
        ):
            with self.subTest(date):
                raw = b"From: a@example.com\r\nDate: " + date + b"\r\n\r\nHi\r\n"

                self.assertGreaterEqual(self._parse(raw, uid).date, before)

    def test_a_date_off_the_calendar_falls_back_to_now(self):
        before = datetime.now(UTC)
        raw = (
            b"From: a@example.com\r\n"
            b"Date: Fri, 31 Dec 9999 23:59:59 -0100\r\n"
            b"\r\nHi\r\n"
        )

        self.assertGreaterEqual(self._parse(raw).date, before)

    def test_a_date_without_a_zone_is_utc(self):
        raw = (
            b"From: a@example.com\r\n"
            b"Date: Wed, 18 Nov 2026 08:00:00 -0000\r\n"
            b"\r\nHi\r\n"
        )

        self.assertEqual(self._parse(raw).date, datetime(2026, 11, 18, 8, tzinfo=UTC))

    def test_a_header_the_parser_chokes_on_is_kept_as_plain_text(self):
        """No input is known to make the standard library raise today; this
        is what happens to the next one."""
        raw = (
            b"From: Alice <alice@example.com>\r\n"
            b"Subject: Still\r\n here\r\n"
            b"\r\nBody\r\n"
        )
        get_all = EmailMessage.get_all

        def choke_on_subject(message, name, failobj=None):
            if name == "Subject":
                raise IndexError("list index out of range")
            return get_all(message, name, failobj)

        with patch.object(EmailMessage, "get_all", choke_on_subject):
            msg = self._parse(raw)

        self.assertEqual(msg.subject, "Still here")
        self.assertEqual(msg.from_email, "alice@example.com")
        self.assertEqual(msg.body_text, "Body\r\n")

    def test_flags_set_the_message_state(self):
        raw = b"From: a@example.com\r\n\r\nHi\r\n"

        msg = _parse_message(
            raw,
            self.account,
            self.folder,
            uid=1,
            flags={"\\Seen", "\\Flagged", "\\Draft"},
        )

        self.assertTrue(msg.is_read)
        self.assertTrue(msg.is_starred)
        self.assertTrue(msg.is_draft)
        unflagged = _parse_message(raw, self.account, self.folder, uid=2, flags=())
        self.assertFalse(unflagged.is_read)
        self.assertFalse(unflagged.is_starred)
        self.assertFalse(unflagged.is_draft)
