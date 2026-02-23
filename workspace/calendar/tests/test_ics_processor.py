from datetime import datetime, timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.calendar.models import Calendar, Event, EventMember
from workspace.calendar.services.ics_processor import process_calendar_email
from workspace.mail.models import MailAccount, MailAttachment, MailFolder, MailMessage

User = get_user_model()

ICS_REQUEST = (
    "BEGIN:VCALENDAR\r\n"
    "METHOD:REQUEST\r\n"
    "PRODID:-//Test//Test//EN\r\n"
    "VERSION:2.0\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:evt-abc-123@example.com\r\n"
    "DTSTART:20260301T140000Z\r\n"
    "DTEND:20260301T150000Z\r\n"
    "SUMMARY:Sprint Review\r\n"
    "DESCRIPTION:Weekly sprint review meeting\r\n"
    "LOCATION:Room 42\r\n"
    "ORGANIZER;CN=Alice:mailto:alice@example.com\r\n"
    "ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:bob@test.com\r\n"
    "SEQUENCE:0\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

ICS_UPDATE = ICS_REQUEST.replace("SEQUENCE:0", "SEQUENCE:2").replace(
    "SUMMARY:Sprint Review", "SUMMARY:Sprint Review (Updated)"
).replace("LOCATION:Room 42", "LOCATION:Room 99")

ICS_CANCEL = (
    "BEGIN:VCALENDAR\r\n"
    "METHOD:CANCEL\r\n"
    "VERSION:2.0\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:evt-abc-123@example.com\r\n"
    "SEQUENCE:3\r\n"
    "DTSTART:20260301T140000Z\r\n"
    "SUMMARY:Sprint Review\r\n"
    "ORGANIZER;CN=Alice:mailto:alice@example.com\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


class ICSProcessorMixin:
    def setUp(self):
        self.user = User.objects.create_user(
            username='bob', email='bob@test.com', password='pass123',
        )
        self.account = MailAccount.objects.create(
            owner=self.user, email='bob@test.com',
            imap_host='imap.test.com', imap_port=993, imap_use_ssl=True,
            smtp_host='smtp.test.com', smtp_port=587, smtp_use_tls=True,
            username='bob@test.com',
        )
        self.account.set_password('pass')
        self.account.save()
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX', display_name='Inbox',
            folder_type='inbox',
        )

    def _create_mail_with_ics(self, ics_data, uid=1):
        mail_msg = MailMessage.objects.create(
            account=self.account, folder=self.folder,
            message_id=f'<msg-{uid}@example.com>', imap_uid=uid,
            subject='Meeting invitation',
            from_address={'email': 'alice@example.com', 'name': 'Alice'},
            to_addresses=[{'email': 'bob@test.com', 'name': 'Bob'}],
            date=datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
            has_calendar_event=True,
        )
        MailAttachment.objects.create(
            message=mail_msg, filename='invite.ics',
            content_type='text/calendar', size=len(ics_data.encode()),
            content=ContentFile(ics_data.encode(), name='invite.ics'),
        )
        return mail_msg


@patch('workspace.calendar.services.ics_processor.notify')
class ProcessRequestTest(ICSProcessorMixin, TestCase):

    def test_creates_event_from_ics_request(self, mock_notify):
        mail_msg = self._create_mail_with_ics(ICS_REQUEST)
        process_calendar_email(mail_msg)

        event = Event.objects.get(ical_uid='evt-abc-123@example.com')
        self.assertEqual(event.title, 'Sprint Review')
        self.assertEqual(event.description, 'Weekly sprint review meeting')
        self.assertEqual(event.location, 'Room 42')
        self.assertEqual(event.organizer_email, 'alice@example.com')
        self.assertEqual(event.ical_sequence, 0)
        self.assertEqual(event.owner, self.user)
        self.assertEqual(event.source_message, mail_msg)
        self.assertEqual(
            event.start,
            datetime(2026, 3, 1, 14, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            event.end,
            datetime(2026, 3, 1, 15, 0, tzinfo=timezone.utc),
        )
        self.assertFalse(event.all_day)

    def test_creates_invitation_calendar_for_account(self, mock_notify):
        mail_msg = self._create_mail_with_ics(ICS_REQUEST)
        process_calendar_email(mail_msg)

        cal = Calendar.objects.get(mail_account=self.account)
        self.assertEqual(cal.name, 'bob@test.com')
        self.assertEqual(cal.color, 'secondary')
        self.assertEqual(cal.owner, self.user)

    def test_reuses_existing_invitation_calendar(self, mock_notify):
        Calendar.objects.create(
            name='Invitations (bob@test.com)',
            color='secondary',
            owner=self.user,
            mail_account=self.account,
        )

        mail_msg = self._create_mail_with_ics(ICS_REQUEST)
        process_calendar_email(mail_msg)

        self.assertEqual(Calendar.objects.filter(mail_account=self.account).count(), 1)

    def test_creates_event_member_pending(self, mock_notify):
        mail_msg = self._create_mail_with_ics(ICS_REQUEST)
        process_calendar_email(mail_msg)

        event = Event.objects.get(ical_uid='evt-abc-123@example.com')
        member = EventMember.objects.get(event=event, user=self.user)
        self.assertEqual(member.status, EventMember.Status.PENDING)

    def test_sends_notification_on_new_event(self, mock_notify):
        mail_msg = self._create_mail_with_ics(ICS_REQUEST)
        process_calendar_email(mail_msg)

        mock_notify.assert_called_once_with(
            recipient=self.user,
            origin='calendar',
            title='Invitation: Sprint Review',
            body='From alice@example.com',
        )

    def test_duplicate_ics_ignored(self, mock_notify):
        mail_msg1 = self._create_mail_with_ics(ICS_REQUEST, uid=1)
        process_calendar_email(mail_msg1)

        mock_notify.reset_mock()

        mail_msg2 = self._create_mail_with_ics(ICS_REQUEST, uid=2)
        process_calendar_email(mail_msg2)

        # Should still have only one event
        self.assertEqual(
            Event.objects.filter(ical_uid='evt-abc-123@example.com').count(), 1,
        )
        # No additional notification for the duplicate
        mock_notify.assert_not_called()


@patch('workspace.calendar.services.ics_processor.notify')
class ProcessUpdateTest(ICSProcessorMixin, TestCase):

    def test_updates_event_with_higher_sequence(self, mock_notify):
        mail_msg1 = self._create_mail_with_ics(ICS_REQUEST, uid=1)
        process_calendar_email(mail_msg1)

        mock_notify.reset_mock()

        mail_msg2 = self._create_mail_with_ics(ICS_UPDATE, uid=2)
        process_calendar_email(mail_msg2)

        event = Event.objects.get(ical_uid='evt-abc-123@example.com')
        self.assertEqual(event.title, 'Sprint Review (Updated)')
        self.assertEqual(event.location, 'Room 99')
        self.assertEqual(event.ical_sequence, 2)
        self.assertEqual(event.source_message, mail_msg2)

        mock_notify.assert_called_once_with(
            recipient=self.user,
            origin='calendar',
            title='Updated: Sprint Review (Updated)',
            body='The event has been updated',
        )

    def test_ignores_lower_sequence(self, mock_notify):
        # First create with the update (sequence=2)
        mail_msg1 = self._create_mail_with_ics(ICS_UPDATE, uid=1)
        process_calendar_email(mail_msg1)

        mock_notify.reset_mock()

        # Then try to process the original (sequence=0) - should be ignored
        mail_msg2 = self._create_mail_with_ics(ICS_REQUEST, uid=2)
        process_calendar_email(mail_msg2)

        event = Event.objects.get(ical_uid='evt-abc-123@example.com')
        # Should still have the updated title
        self.assertEqual(event.title, 'Sprint Review (Updated)')
        self.assertEqual(event.ical_sequence, 2)
        mock_notify.assert_not_called()


@patch('workspace.calendar.services.ics_processor.notify')
class ProcessCancelTest(ICSProcessorMixin, TestCase):

    def test_cancel_marks_event_cancelled(self, mock_notify):
        mail_msg1 = self._create_mail_with_ics(ICS_REQUEST, uid=1)
        process_calendar_email(mail_msg1)

        mock_notify.reset_mock()

        mail_msg2 = self._create_mail_with_ics(ICS_CANCEL, uid=2)
        process_calendar_email(mail_msg2)

        event = Event.objects.get(ical_uid='evt-abc-123@example.com')
        self.assertTrue(event.is_cancelled)

    def test_cancel_notifies_user(self, mock_notify):
        mail_msg1 = self._create_mail_with_ics(ICS_REQUEST, uid=1)
        process_calendar_email(mail_msg1)

        mock_notify.reset_mock()

        mail_msg2 = self._create_mail_with_ics(ICS_CANCEL, uid=2)
        process_calendar_email(mail_msg2)

        mock_notify.assert_called_once_with(
            recipient=self.user,
            origin='calendar',
            title='Cancelled: Sprint Review',
        )
