"""Pictures an HTML body embeds from its own message (``cid:``).

They survive the sanitizer at import, and the detail endpoint points each one
at the message's own attachment. A reference to anything else is dropped.
"""

from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from workspace.mail.models import MailAccount, MailAttachment, MailFolder, MailMessage
from workspace.mail.services.imap_parse import _parse_message
from workspace.mail.services.remote_images import has_remote_images

User = get_user_model()

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class InlineImageFixtures(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="inline", password="pass")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
        )
        self.inbox = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _message(self, body_html, uid=1):
        return MailMessage.objects.create(
            account=self.account,
            folder=self.inbox,
            imap_uid=uid,
            date=timezone.now(),
            body_html=body_html,
        )

    def _attachment(self, message, content_id, filename="logo.png", is_inline=True):
        return MailAttachment.objects.create(
            message=message,
            filename=filename,
            content_type="image/png",
            size=len(PNG),
            content=ContentFile(PNG, name=filename),
            content_id=content_id,
            is_inline=is_inline,
        )

    def _detail(self, message):
        response = self.client.get(f"/api/v1/mail/messages/{message.uuid}")
        self.assertEqual(response.status_code, 200)
        return response.json()


class CidSurvivesImportTests(InlineImageFixtures):
    def test_a_related_image_keeps_its_cid_reference(self):
        raw = MIMEMultipart("related")
        raw["Subject"] = "Logo"
        raw["From"] = "sender@example.com"
        raw["Message-ID"] = "<logo-001@example.com>"
        raw.attach(MIMEText('<p>Hi</p><img src="cid:logo@example.com">', "html"))
        image = MIMEImage(PNG, _subtype="png")
        image.add_header("Content-ID", "<logo@example.com>")
        raw.attach(image)

        message = _parse_message(raw.as_bytes(), self.account, self.inbox, 1, "")

        self.assertIn('src="cid:logo@example.com"', message.body_html)
        attachment = message.attachments.get()
        self.assertEqual(attachment.content_id, "logo@example.com")


class CidResolvedOnReadTests(InlineImageFixtures):
    def test_a_known_cid_points_at_its_attachment(self):
        message = self._message('<p>Hi</p><img src="cid:logo@example.com" alt="Logo">')
        logo = self._attachment(message, "logo@example.com")

        body = self._detail(message)["body_html"]

        self.assertIn(f'src="/api/v1/mail/attachments/{logo.uuid}"', body)
        self.assertNotIn("cid:", body)

    def test_a_percent_encoded_cid_resolves(self):
        message = self._message('<img src="cid:logo%40example.com">')
        logo = self._attachment(message, "logo@example.com")

        body = self._detail(message)["body_html"]

        self.assertIn(f"/api/v1/mail/attachments/{logo.uuid}", body)

    def test_an_unknown_cid_is_stripped(self):
        message = self._message('<img src="cid:missing@example.com" alt="gone">')
        self._attachment(message, "logo@example.com")

        body = self._detail(message)["body_html"]

        self.assertEqual(body, '<img alt="gone">')

    def test_another_messages_attachment_is_never_referenced(self):
        message = self._message('<img src="cid:theirs@example.com">', uid=1)
        other = self._message("<p>Other</p>", uid=2)
        theirs = self._attachment(other, "theirs@example.com")

        body = self._detail(message)["body_html"]

        self.assertNotIn(str(theirs.uuid), body)
        self.assertNotIn("src=", body)

    def test_a_cid_link_is_dropped(self):
        message = self._message('<a href="cid:logo@example.com">logo</a>')
        self._attachment(message, "logo@example.com")

        body = self._detail(message)["body_html"]

        self.assertNotIn("href", body)
        self.assertIn(">logo</a>", body)

    def test_the_pictures_the_body_shows_are_marked_embedded(self):
        message = self._message('<img src="cid:logo@example.com">')
        logo = self._attachment(message, "logo@example.com")
        report = self._attachment(message, "", filename="report.pdf", is_inline=False)
        unused = self._attachment(message, "unused@example.com", filename="unused.png")

        attachments = {a["uuid"]: a for a in self._detail(message)["attachments"]}

        self.assertTrue(attachments[str(logo.uuid)]["embedded"])
        self.assertFalse(attachments[str(report.uuid)]["embedded"])
        self.assertFalse(attachments[str(unused.uuid)]["embedded"])

    def test_cid_pictures_are_not_remote(self):
        message = self._message('<img src="cid:logo@example.com">')
        self._attachment(message, "logo@example.com")

        self.assertFalse(self._detail(message)["has_remote_images"])


class CidIsNotRemoteTests(TestCase):
    def test_a_cid_img_is_not_remote(self):
        self.assertFalse(has_remote_images('<img src="cid:logo@example.com">'))

    def test_a_cid_css_background_is_not_remote(self):
        self.assertFalse(
            has_remote_images('<td style="background: url(cid:bg@example.com)">x</td>')
        )
