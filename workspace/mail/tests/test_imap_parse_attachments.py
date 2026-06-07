from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailAttachment, MailFolder
from workspace.mail.services.imap_parse import _parse_message

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

        mail_msg = _parse_message(raw, self.account, self.folder, uid=1, flags_str="")

        self.assertIsNotNone(mail_msg)
        attachments = MailAttachment.objects.filter(message=mail_msg)
        self.assertEqual(attachments.count(), 1)

        att = attachments.first()
        self.assertEqual(att.filename, "empty.bin")
        self.assertEqual(att.size, 0)
        self.assertEqual(att.content.read(), b"")
