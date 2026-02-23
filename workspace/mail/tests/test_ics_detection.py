from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailAttachment, MailFolder, MailMessage
from workspace.mail.services.imap import _parse_message

User = get_user_model()

ICS_CONTENT = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
DTSTART:20260301T100000Z
DTEND:20260301T110000Z
SUMMARY:Team Meeting
ORGANIZER:mailto:organizer@example.com
ATTENDEE:mailto:user@example.com
END:VEVENT
END:VCALENDAR"""


class ICSDetectionMixin:
    """Common setup for ICS detection tests."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='icsuser', email='ics@test.com', password='pass123',
        )
        self.account = MailAccount.objects.create(
            owner=self.user,
            email='user@example.com',
            imap_host='imap.example.com',
            imap_use_ssl=True,
            smtp_host='smtp.example.com',
            username='user@example.com',
        )
        self.account.set_password('secret')
        self.account.save()
        self.folder = MailFolder.objects.create(
            account=self.account,
            name='INBOX',
            display_name='Inbox',
            folder_type='inbox',
        )

    def _build_calendar_email(self):
        """Build a multipart email with a text/calendar part."""
        msg = MIMEMultipart('mixed')
        msg['Subject'] = 'Meeting Invitation'
        msg['From'] = 'organizer@example.com'
        msg['To'] = 'user@example.com'
        msg['Message-ID'] = '<cal-test-001@example.com>'
        msg['Date'] = 'Mon, 23 Feb 2026 10:00:00 +0000'

        text_part = MIMEText('You have been invited to a meeting.', 'plain')
        msg.attach(text_part)

        calendar_part = MIMEText(ICS_CONTENT, 'calendar', 'utf-8')
        msg.attach(calendar_part)

        return msg

    def _build_normal_email(self):
        """Build a normal multipart email without calendar parts."""
        msg = MIMEMultipart('mixed')
        msg['Subject'] = 'Hello'
        msg['From'] = 'sender@example.com'
        msg['To'] = 'user@example.com'
        msg['Message-ID'] = '<normal-001@example.com>'
        msg['Date'] = 'Mon, 23 Feb 2026 10:00:00 +0000'

        text_part = MIMEText('Just a regular email.', 'plain')
        msg.attach(text_part)

        html_part = MIMEText('<p>Just a regular email.</p>', 'html')
        msg.attach(html_part)

        return msg


class TestICSEmailFlagsHasCalendarEvent(ICSDetectionMixin, TestCase):
    """Email with text/calendar part sets has_calendar_event=True."""

    def test_ics_email_flags_has_calendar_event(self):
        msg = self._build_calendar_email()
        raw = msg.as_bytes()

        mail_msg = _parse_message(raw, self.account, self.folder, uid=1, flags_str='')

        self.assertIsNotNone(mail_msg)
        self.assertTrue(mail_msg.has_calendar_event)


class TestICSEmailStoresCalendarAttachment(ICSDetectionMixin, TestCase):
    """Email with text/calendar part creates a MailAttachment."""

    def test_ics_email_stores_calendar_attachment(self):
        msg = self._build_calendar_email()
        raw = msg.as_bytes()

        mail_msg = _parse_message(raw, self.account, self.folder, uid=2, flags_str='')

        self.assertIsNotNone(mail_msg)
        attachments = MailAttachment.objects.filter(message=mail_msg)
        self.assertEqual(attachments.count(), 1)

        att = attachments.first()
        self.assertEqual(att.content_type, 'text/calendar')
        self.assertEqual(att.filename, 'invite.ics')
        self.assertIn('BEGIN:VCALENDAR', att.content.read().decode('utf-8'))


class TestNormalEmailNotFlagged(ICSDetectionMixin, TestCase):
    """Regular email without text/calendar has has_calendar_event=False."""

    def test_normal_email_not_flagged(self):
        msg = self._build_normal_email()
        raw = msg.as_bytes()

        mail_msg = _parse_message(raw, self.account, self.folder, uid=3, flags_str='')

        self.assertIsNotNone(mail_msg)
        self.assertFalse(mail_msg.has_calendar_event)
        self.assertEqual(MailAttachment.objects.filter(message=mail_msg).count(), 0)
