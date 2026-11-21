import base64
import io
from email.message import EmailMessage
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.policy import SMTP

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailAttachment, MailFolder, MailMessage
from workspace.mail.services.imap_parse import _parse_message
from workspace.mail.services.smtp import build_draft_message

User = get_user_model()


class ZeroByteAttachmentTests(TestCase):
    """A zero-byte attachment must still be persisted, not silently dropped."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="zerobyte",
            email="z@test.com",
            password="pass123",
        )
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@example.com",
            imap_host="imap.example.com",
            imap_use_ssl=True,
            smtp_host="smtp.example.com",
            username="user@example.com",
        )
        self.account.set_password("secret")
        self.account.save()
        self.folder = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )

    def _build_email_with_empty_attachment(self):
        msg = MIMEMultipart("mixed")
        msg["Subject"] = "Empty file attached"
        msg["From"] = "sender@example.com"
        msg["To"] = "user@example.com"
        msg["Message-ID"] = "<empty-att-001@example.com>"
        msg["Date"] = "Mon, 23 Feb 2026 10:00:00 +0000"

        msg.attach(MIMEText("See attached.", "plain"))

        empty_part = MIMEApplication(b"", _subtype="octet-stream")
        empty_part.add_header(
            "Content-Disposition",
            "attachment",
            filename="empty.bin",
        )
        msg.attach(empty_part)

        return msg

    def test_zero_byte_attachment_is_persisted(self):
        raw = self._build_email_with_empty_attachment().as_bytes()

        mail_msg = _parse_message(raw, self.account, self.folder, uid=1, flags=())

        self.assertIsNotNone(mail_msg)
        attachments = MailAttachment.objects.filter(message=mail_msg)
        self.assertEqual(attachments.count(), 1)

        att = attachments.first()
        self.assertEqual(att.filename, "empty.bin")
        self.assertEqual(att.size, 0)
        self.assertEqual(att.content.read(), b"")

    def test_a_message_built_here_keeps_its_attachments_when_read_back(self):
        """The Sent copy and every draft are parsed from what
        build_draft_message wrote: a name the parser cannot read back is an
        attachment missing from the user's own copy."""
        names = {
            "r\u00e9union.pdf": "application/pdf",
            'rapport "final".pdf': "application/pdf",
            "\u65e5\u672c\u8a9e.txt": "text/plain",
            "photo.jpg": "image/jpeg",
        }
        attachments = []
        for name in names:
            attachment = io.BytesIO(name.encode())
            attachment.name = name
            attachments.append(attachment)
        raw = build_draft_message(
            self.account,
            to=["bob@example.com"],
            subject="Four files",
            body_text="See attached.",
            attachments=attachments,
        )

        mail_msg = _parse_message(raw, self.account, self.folder, uid=1, flags=())

        stored = {
            a.filename: (a.content_type, a.content.read(), a.is_inline)
            for a in MailAttachment.objects.filter(message=mail_msg)
        }
        self.assertEqual(
            stored,
            {
                name: (content_type, name.encode(), False)
                for name, content_type in names.items()
            },
        )
        self.assertTrue(mail_msg.has_attachments)
        self.assertEqual(mail_msg.body_text.strip(), "See attached.")


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

ICS = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\n"
    "UID:sort-1@example.com\r\nDTSTART:20261120T140000Z\r\n"
    "DTEND:20261120T150000Z\r\nSUMMARY:Point\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
)


def _message(subject="Parts"):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = "sender@example.com"
    msg["To"] = "user@example.com"
    msg["Date"] = "Mon, 23 Feb 2026 10:00:00 +0000"
    return msg


def _part(content, **kwargs):
    part = EmailMessage()
    part.set_content(content, **kwargs)
    return part


class PartSortingTests(TestCase):
    """Which parts of a message import keeps as attachments, which it reads
    as body, and when the message is said to carry attachments."""

    def setUp(self):
        self.user = User.objects.create_user(username="parts", password="pass")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
        )
        self.folder = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )

    def parse(self, raw):
        if isinstance(raw, EmailMessage):
            raw = raw.as_bytes()
        return _parse_message(raw, self.account, self.folder, uid=1, flags=())

    def attachments(self, message):
        return [
            (a.filename, a.content_type, a.is_inline)
            for a in message.attachments.order_by("created_at", "uuid")
        ]

    def test_an_inline_file_without_a_content_id_is_an_attachment(self):
        msg = _message()
        msg.set_content("See the report.")
        msg.add_attachment(
            b"%PDF-1.4",
            maintype="application",
            subtype="pdf",
            filename="report.pdf",
            disposition="inline",
        )

        message = self.parse(msg)

        self.assertEqual(
            self.attachments(message), [("report.pdf", "application/pdf", False)]
        )
        self.assertTrue(message.has_attachments)

    def test_a_named_inline_text_file_is_an_attachment_not_body(self):
        msg = _message()
        msg.set_content("Notes attached.")
        msg.add_attachment("line one\n", filename="notes.txt", disposition="inline")

        message = self.parse(msg)

        self.assertEqual(
            self.attachments(message), [("notes.txt", "text/plain", False)]
        )
        self.assertEqual(message.body_text.strip(), "Notes attached.")

    def test_text_after_an_inline_file_stays_in_the_body(self):
        msg = _message()
        msg.set_content("Before the picture.")
        msg.add_attachment(
            PNG,
            maintype="image",
            subtype="png",
            filename="photo.png",
            disposition="inline",
        )
        msg.attach(_part("After the picture."))

        message = self.parse(msg)

        self.assertEqual(
            message.body_text.split(), "Before the picture. After the picture.".split()
        )
        self.assertEqual(self.attachments(message), [("photo.png", "image/png", False)])

    def test_an_attached_message_is_an_eml_file_with_its_own_files(self):
        attached = _message("Invoice 114")
        attached.set_content("The invoice.")
        attached.add_attachment(
            b"%PDF-1.4", maintype="application", subtype="pdf", filename="114.pdf"
        )
        msg = _message("Fwd: Invoice 114")
        msg.set_content("Forwarding.")
        msg.add_attachment(attached)

        message = self.parse(msg)

        self.assertEqual(
            self.attachments(message), [("Invoice 114.eml", "message/rfc822", False)]
        )
        self.assertTrue(message.has_attachments)
        with message.attachments.get().content.open("rb") as content:
            self.assertEqual(content.read(), attached.as_bytes(policy=SMTP))

    def test_an_attached_message_sent_base64_is_stored_decoded(self):
        attached = _message("Invoice 115")
        attached.set_content("The invoice.")
        source = attached.as_bytes(policy=SMTP)
        raw = (
            b"From: sender@example.com\r\nTo: user@example.com\r\n"
            b"Subject: Fwd\r\nMIME-Version: 1.0\r\n"
            b'Content-Type: multipart/mixed; boundary="b"\r\n\r\n'
            b"--b\r\nContent-Type: text/plain\r\n\r\nForwarding.\r\n"
            b"--b\r\nContent-Type: message/rfc822\r\n"
            b"Content-Transfer-Encoding: base64\r\n\r\n"
            + base64.encodebytes(source)
            + b"--b--\r\n"
        )

        message = self.parse(raw)

        self.assertEqual(
            self.attachments(message), [("Invoice 115.eml", "message/rfc822", False)]
        )
        with message.attachments.get().content.open("rb") as content:
            self.assertEqual(content.read(), source)

    def test_pictures_the_body_shows_are_not_attachments(self):
        msg = _message()
        msg.set_content("Logo below.")
        msg.add_alternative(
            '<p>Logo below.</p><img src="cid:logo@example.com">', "html"
        )
        msg.get_payload()[1].add_related(
            PNG, maintype="image", subtype="png", cid="<logo@example.com>"
        )

        message = self.parse(msg)

        self.assertEqual(
            self.attachments(message), [("attachment.png", "image/png", True)]
        )
        self.assertFalse(message.has_attachments)

    def test_a_picture_the_body_does_not_show_is_an_attachment(self):
        msg = _message()
        msg.set_content("Logo below.")
        msg.add_alternative("<p>No picture here.</p>", "html")
        msg.get_payload()[1].add_related(
            PNG, maintype="image", subtype="png", cid="<logo@example.com>"
        )

        message = self.parse(msg)

        self.assertTrue(message.has_attachments)

    def test_an_invitation_alone_is_not_an_attachment(self):
        msg = _message()
        msg.set_content("You are invited.")
        msg.add_alternative("<p>You are invited.</p>", "html")
        msg.add_alternative(ICS, "calendar", params={"method": "REQUEST"})

        message = self.parse(msg)

        self.assertTrue(message.has_calendar_event)
        self.assertEqual(
            self.attachments(message), [("invite.ics", "text/calendar", False)]
        )
        self.assertFalse(message.has_attachments)

    def test_an_attached_message_that_holds_nothing_still_imports(self):
        for encoding, body in (("7bit", b""), ("base64", b"not base64 at all !")):
            with self.subTest(encoding):
                raw = (
                    b"From: sender@example.com\r\nSubject: Fwd\r\nMIME-Version: 1.0\r\n"
                    b'Content-Type: multipart/mixed; boundary="b"\r\n\r\n'
                    b"--b\r\nContent-Type: text/plain\r\n\r\nForwarding.\r\n"
                    b"--b\r\nContent-Type: message/rfc822\r\n"
                    b"Content-Transfer-Encoding: "
                    + encoding.encode()
                    + b"\r\n\r\n"
                    + body
                    + b"\r\n--b--\r\n"
                )
                MailMessage.objects.all().delete()

                message = self.parse(raw)

                self.assertEqual(message.body_text.strip(), "Forwarding.")
                self.assertEqual(
                    self.attachments(message),
                    [("message.eml", "message/rfc822", False)],
                )
