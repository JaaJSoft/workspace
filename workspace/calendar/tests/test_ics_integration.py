"""End-to-end test: .ics email received -> event created -> user responds -> REPLY sent."""

from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.calendar.models import Calendar, Event, EventMember
from workspace.mail.models import MailAccount, MailFolder

User = get_user_model()

ICS_REQUEST = (
    "BEGIN:VCALENDAR\r\n"
    "METHOD:REQUEST\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//EN\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:e2e-test-uid@example.com\r\n"
    "DTSTART:20260315T090000Z\r\n"
    "DTEND:20260315T100000Z\r\n"
    "SUMMARY:Design Review\r\n"
    "LOCATION:Conference Room B\r\n"
    "ORGANIZER;CN=External User:mailto:ext@company.com\r\n"
    "ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:me@workspace.com\r\n"
    "SEQUENCE:0\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def _build_raw_email():
    msg = MIMEMultipart('mixed')
    msg['From'] = 'ext@company.com'
    msg['To'] = 'me@workspace.com'
    msg['Subject'] = 'Design Review invitation'
    msg['Message-ID'] = '<e2e-invite@company.com>'
    msg['Date'] = 'Sun, 15 Mar 2026 08:00:00 +0000'
    msg.attach(MIMEText('Please join the design review.', 'plain'))
    cal_part = MIMEText(ICS_REQUEST, 'calendar', 'utf-8')
    cal_part.set_param('method', 'REQUEST')
    msg.attach(cal_part)
    return msg.as_bytes()


class EndToEndICSTest(APITestCase):
    """Full flow: receive .ics email -> create event -> accept -> send REPLY."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='me', email='me@workspace.com', password='pass123',
        )
        self.account = MailAccount.objects.create(
            owner=self.user, email='me@workspace.com',
            imap_host='imap.test.com', imap_port=993, imap_use_ssl=True,
            smtp_host='smtp.test.com', smtp_port=587, smtp_use_tls=True,
            username='me@workspace.com',
        )
        self.account.set_password('pass')
        self.account.save()
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX',
            display_name='Inbox', folder_type='inbox',
        )

    @patch('workspace.calendar.services.ics_processor.notify')
    def test_full_flow_receive_and_accept(self, mock_notify):
        # Step 1: Parse the email (simulates what IMAP sync does)
        from workspace.mail.services.imap import _parse_message

        raw = _build_raw_email()
        mail_msg = _parse_message(raw, self.account, self.folder, uid=1, flags_str='')

        # Verify email was flagged
        self.assertTrue(mail_msg.has_calendar_event)

        # Step 2: Process the calendar event
        from workspace.calendar.services.ics_processor import process_calendar_email
        process_calendar_email(mail_msg)

        # Verify event was created
        event = Event.objects.get(ical_uid='e2e-test-uid@example.com')
        self.assertEqual(event.title, 'Design Review')
        self.assertEqual(event.location, 'Conference Room B')
        self.assertEqual(event.organizer_email, 'ext@company.com')

        # Verify invitation calendar was created
        cal = Calendar.objects.get(mail_account=self.account)
        self.assertEqual(event.calendar, cal)

        # Verify EventMember exists
        member = EventMember.objects.get(event=event, user=self.user)
        self.assertEqual(member.status, 'pending')

        # Step 3: User accepts the invitation
        self.client.force_authenticate(self.user)
        with patch('workspace.calendar.views.send_ics_reply') as mock_reply:
            url = f'/api/v1/calendar/events/{event.pk}/respond'
            resp = self.client.post(url, {'status': 'accepted'}, format='json')
            self.assertEqual(resp.status_code, status.HTTP_200_OK)

            # Verify REPLY would be sent
            mock_reply.assert_called_once_with(event, self.user, 'accepted')

        # Verify member status updated
        member.refresh_from_db()
        self.assertEqual(member.status, 'accepted')
