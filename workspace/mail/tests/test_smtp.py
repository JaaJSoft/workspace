import io
import smtplib
from email import message_from_bytes, message_from_string
from unittest.mock import MagicMock, patch

from django.test import TestCase

from workspace.mail.services.smtp import (
    SMTP_TIMEOUT,
    _transmit,
    build_draft_message,
    connect_smtp,
    send_email,
    test_smtp_connection,
)
from workspace.mail.tests.smtp_recorder import RecordingSMTP

# ── build_draft_message ─────────────────────────────────────────


class BuildDraftMessageTests(TestCase):
    def _make_account(self):
        acct = MagicMock()
        acct.display_name = "Alice"
        acct.email = "alice@example.com"
        return acct

    def test_basic_message_structure(self):
        acct = self._make_account()
        raw = build_draft_message(
            acct,
            to=["bob@example.com"],
            subject="Hello",
            body_text="Hi Bob",
        )
        msg = message_from_string(raw.decode("utf-8"))
        self.assertEqual(msg["To"], "bob@example.com")
        self.assertEqual(msg["Subject"], "Hello")
        self.assertIn("alice@example.com", msg["From"])

    def test_cc_header(self):
        acct = self._make_account()
        raw = build_draft_message(
            acct,
            to=["bob@example.com"],
            subject="Test",
            cc=["carol@example.com"],
        )
        msg = message_from_string(raw.decode("utf-8"))
        self.assertEqual(msg["Cc"], "carol@example.com")

    def test_html_body(self):
        acct = self._make_account()
        raw = build_draft_message(
            acct,
            to=["bob@example.com"],
            subject="HTML",
            body_html="<h1>Hello</h1>",
        )
        msg = message_from_string(raw.decode("utf-8"))
        # HTML body is in the multipart/alternative part, possibly base64-encoded
        self.assertIn("text/html", msg.as_string())

    def test_reply_to_header(self):
        acct = self._make_account()
        raw = build_draft_message(
            acct,
            to=["bob@example.com"],
            subject="Re",
            reply_to="noreply@example.com",
        )
        msg = message_from_string(raw.decode("utf-8"))
        self.assertEqual(msg["Reply-To"], "noreply@example.com")

    def test_bcc_header_included_when_requested(self):
        """Drafts must persist Bcc in the header: the draft is APPENDed to
        IMAP and re-parsed on open, so the header is the only place the
        Bcc list can survive a save/reopen round-trip."""
        acct = self._make_account()
        raw = build_draft_message(
            acct,
            to=["bob@example.com"],
            subject="Draft",
            bcc=["dave@example.com", "eve@example.com"],
            include_bcc=True,
        )
        msg = message_from_string(raw.decode("utf-8"))
        self.assertEqual(msg["Bcc"], "dave@example.com, eve@example.com")

    def test_bcc_header_omitted_by_default(self):
        """The send path must never write a Bcc header - recipients would
        leak to everyone. Bcc travels in the SMTP envelope only."""
        acct = self._make_account()
        raw = build_draft_message(
            acct,
            to=["bob@example.com"],
            subject="Send",
            bcc=["dave@example.com"],
        )
        msg = message_from_string(raw.decode("utf-8"))
        self.assertIsNone(msg["Bcc"])

    def test_threading_headers(self):
        acct = self._make_account()
        raw = build_draft_message(
            acct,
            to=["bob@example.com"],
            subject="Re: Hello",
            in_reply_to="<parent@example.com>",
            references="<root@example.com> <parent@example.com>",
        )
        msg = message_from_string(raw.decode("utf-8"))
        self.assertEqual(msg["In-Reply-To"], "<parent@example.com>")
        self.assertEqual(msg["References"], "<root@example.com> <parent@example.com>")

    def test_threading_headers_omitted_on_a_fresh_message(self):
        acct = self._make_account()
        raw = build_draft_message(acct, to=["bob@example.com"], subject="Hello")
        msg = message_from_string(raw.decode("utf-8"))
        self.assertIsNone(msg["In-Reply-To"])
        self.assertIsNone(msg["References"])

    def test_message_id_uses_domain(self):
        acct = self._make_account()
        raw = build_draft_message(acct, to=["bob@example.com"], subject="Test")
        msg = message_from_string(raw.decode("utf-8"))
        self.assertIn("example.com", msg["Message-ID"])

    def test_attachments(self):
        acct = self._make_account()
        attachment = io.BytesIO(b"%PDF-fake")
        attachment.name = "doc.pdf"
        raw = build_draft_message(
            acct,
            to=["bob@example.com"],
            subject="With attachment",
            attachments=[attachment],
        )
        (part,) = [p for p in message_from_bytes(raw).walk() if p.get_filename()]
        self.assertEqual(part.get_filename(), "doc.pdf")
        self.assertEqual(part.get_payload(decode=True), b"%PDF-fake")

    def test_attachment_read_in_uneven_chunks_round_trips(self):
        # A storage stream may return fewer bytes than asked for; the base64
        # lines must still come out whole, with no padding mid-payload.
        payload = bytes(range(256)) * 1000

        class ShortReads(io.BytesIO):
            def read(self, size=-1):
                return super().read(min(size, 1000) if size > 0 else size)

        attachment = ShortReads(payload)
        attachment.name = "blob.bin"
        raw = build_draft_message(
            self._make_account(), to=["bob@example.com"], attachments=[attachment]
        )

        (part,) = [p for p in message_from_bytes(raw).walk() if p.get_filename()]
        self.assertEqual(part.get_payload(decode=True), payload)
        encoded_lines = part.get_payload().splitlines()
        self.assertTrue(all(len(line) == 76 for line in encoded_lines[:-1]))
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))


# ── connect_smtp ────────────────────────────────────────────────


class ConnectSmtpTests(TestCase):
    @patch("workspace.mail.services.smtp.smtplib.SMTP")
    def test_tls_connection(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server
        acct = MagicMock()
        acct.smtp_use_tls = True
        acct.smtp_host = "smtp.example.com"
        acct.smtp_port = 587
        acct.auth_method = "password"
        acct.username = "alice"
        acct.get_password.return_value = "secret"

        server = connect_smtp(acct)

        # Without an explicit deadline smtplib waits forever, and sending now
        # happens inline in the AI tool loop.
        mock_smtp_cls.assert_called_with("smtp.example.com", 587, timeout=SMTP_TIMEOUT)
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_with("alice", "secret")
        self.assertEqual(server, mock_server)

    @patch("workspace.mail.services.smtp.smtplib.SMTP_SSL")
    def test_ssl_connection(self, mock_smtp_ssl_cls):
        mock_server = MagicMock()
        mock_smtp_ssl_cls.return_value = mock_server
        acct = MagicMock()
        acct.smtp_use_tls = False
        acct.smtp_host = "smtp.example.com"
        acct.smtp_port = 465
        acct.auth_method = "password"
        acct.username = "alice"
        acct.get_password.return_value = "secret"

        connect_smtp(acct)

        mock_smtp_ssl_cls.assert_called_with(
            "smtp.example.com", 465, timeout=SMTP_TIMEOUT
        )
        mock_server.login.assert_called_with("alice", "secret")


# ── test_smtp_connection ────────────────────────────────────────


class TestSmtpConnectionTests(TestCase):
    @patch("workspace.mail.services.smtp.connect_smtp")
    def test_success(self, mock_connect):
        mock_server = MagicMock()
        mock_connect.return_value = mock_server
        success, err = test_smtp_connection(MagicMock())
        self.assertTrue(success)
        self.assertIsNone(err)
        mock_server.quit.assert_called_once()

    @patch("workspace.mail.services.smtp.connect_smtp")
    def test_failure(self, mock_connect):
        mock_connect.side_effect = Exception("Connection refused")
        success, err = test_smtp_connection(MagicMock())
        self.assertFalse(success)
        self.assertIn("Connection refused", err)


# ── send_email ──────────────────────────────────────────────────


class SendEmailTests(TestCase):
    def _make_account(self):
        acct = MagicMock()
        acct.display_name = "Alice"
        acct.email = "alice@example.com"
        return acct

    @patch("workspace.mail.services.smtp.connect_smtp")
    def test_sends_to_all_recipients(self, mock_connect):
        server = RecordingSMTP()
        mock_connect.return_value = server

        send_email(
            self._make_account(),
            to=["bob@example.com"],
            subject="Test",
            body_text="Hello",
            cc=["carol@example.com"],
        ).close()

        self.assertEqual(server.transactions, 1)
        self.assertEqual(server.sender, "alice@example.com")
        self.assertEqual(server.recipients, ["bob@example.com", "carol@example.com"])
        self.assertEqual(server.mail_options, [f"size={len(server.raw_message)}"])
        self.assertTrue(server.quit_called)

    @patch("workspace.mail.services.smtp.connect_smtp")
    def test_threading_headers_reach_the_wire(self, mock_connect):
        server = RecordingSMTP()
        mock_connect.return_value = server

        send_email(
            self._make_account(),
            to=["bob@example.com"],
            subject="Re: Test",
            body_text="Hello",
            in_reply_to="<parent@example.com>",
            references="<root@example.com> <parent@example.com>",
        ).close()

        sent = server.message
        self.assertEqual(sent["In-Reply-To"], "<parent@example.com>")
        self.assertEqual(sent["References"], "<root@example.com> <parent@example.com>")

    @patch("workspace.mail.services.smtp.connect_smtp")
    def test_attachment_reaches_the_wire_intact(self, mock_connect):
        server = RecordingSMTP()
        mock_connect.return_value = server
        payload = b"%PDF-" + bytes(range(256)) * 300
        attachment = io.BytesIO(payload)
        attachment.name = "report.pdf"

        send_email(
            self._make_account(),
            to=["bob@example.com"],
            subject="Report",
            body_text="Attached",
            attachments=[attachment],
        ).close()

        (part,) = [p for p in server.message.walk() if p.get_filename()]
        self.assertEqual(part.get_filename(), "report.pdf")
        self.assertEqual(part.get_payload(decode=True), payload)

    @patch("workspace.mail.services.smtp.connect_smtp")
    def test_the_message_file_is_closed_when_smtp_fails(self, mock_connect):
        server = RecordingSMTP()
        server.mail = MagicMock(return_value=(550, b"Sender rejected"))
        mock_connect.return_value = server

        with (
            patch("workspace.mail.services.smtp.tempfile.TemporaryFile") as temp,
            self.assertRaises(smtplib.SMTPSenderRefused),
        ):
            temp.return_value = file = io.BytesIO()
            send_email(self._make_account(), to=["bob@example.com"], subject="Hi")

        self.assertTrue(file.closed)
        self.assertTrue(server.quit_called)


class TransmitTests(TestCase):
    def test_lines_starting_with_a_dot_are_stuffed(self):
        server = RecordingSMTP()
        message = b"Subject: dots\r\n\r\n.hidden\r\n..twice\r\nplain\r\n"

        _transmit(server, "a@example.com", ["b@example.com"], io.BytesIO(message))

        self.assertEqual(
            server.data,
            b"Subject: dots\r\n\r\n..hidden\r\n...twice\r\nplain\r\n.\r\n",
        )
        self.assertEqual(server.raw_message, message)

    def test_a_message_without_a_final_line_break_is_terminated(self):
        server = RecordingSMTP()

        _transmit(server, "a@example.com", ["b@example.com"], io.BytesIO(b"x\r\ny"))

        self.assertEqual(server.data, b"x\r\ny\r\n.\r\n")

    def test_every_recipient_refused_aborts_before_data(self):
        server = RecordingSMTP()
        server.rcpt = MagicMock(return_value=(550, b"No such user"))

        with self.assertRaises(smtplib.SMTPRecipientsRefused):
            _transmit(server, "a@example.com", ["b@example.com"], io.BytesIO(b"x"))
        self.assertEqual(server.data, b"")

    def test_some_recipients_refused_still_sends_and_reports_them(self):
        server = RecordingSMTP()
        server.rcpt = MagicMock(side_effect=[(250, b"OK"), (550, b"No such user")])

        refused = _transmit(
            server,
            "a@example.com",
            ["b@example.com", "ghost@example.com"],
            io.BytesIO(b"x\r\n"),
        )

        self.assertEqual(refused, {"ghost@example.com": (550, b"No such user")})
        self.assertEqual(server.transactions, 1)

    def test_a_rejected_message_raises(self):
        server = RecordingSMTP()
        server.getreply = MagicMock(return_value=(552, b"Message too large"))

        with self.assertRaises(smtplib.SMTPDataError):
            _transmit(server, "a@example.com", ["b@example.com"], io.BytesIO(b"x"))
