import io
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailAttachment, MailFolder
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
