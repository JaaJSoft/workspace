import icalendar
from datetime import datetime, timezone

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.calendar.models import Calendar, Event
from workspace.mail.models import MailAccount, MailFolder, MailMessage

User = get_user_model()


class ICSBuilderTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='bob', email='bob@test.com', password='pass123')
        self.account = MailAccount.objects.create(
            owner=self.user, email='bob@test.com',
            imap_host='imap.test.com', imap_port=993, imap_use_ssl=True,
            smtp_host='smtp.test.com', smtp_port=587, smtp_use_tls=True,
            username='bob@test.com',
        )
        self.account.set_password('pass')
        self.account.save()
        self.calendar = Calendar.objects.create(
            name='Invitations', owner=self.user, mail_account=self.account,
        )
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX', display_name='Inbox', folder_type='inbox',
        )
        self.mail_msg = MailMessage.objects.create(
            account=self.account, folder=self.folder,
            message_id='<invite@example.com>', imap_uid=1,
            subject='Invite', date=datetime(2026, 3, 1, tzinfo=timezone.utc),
        )
        self.event = Event.objects.create(
            calendar=self.calendar, title='Sprint Review',
            start=datetime(2026, 3, 1, 14, 0, tzinfo=timezone.utc),
            end=datetime(2026, 3, 1, 15, 0, tzinfo=timezone.utc),
            owner=self.user, ical_uid='evt-123@example.com',
            ical_sequence=0, organizer_email='alice@example.com',
            source_message=self.mail_msg,
        )

    def test_build_reply_accepted(self):
        from workspace.calendar.services.ics_builder import build_reply
        ics_bytes = build_reply(self.event, self.user, 'accepted')
        cal = icalendar.Calendar.from_ical(ics_bytes)
        self.assertEqual(str(cal['METHOD']), 'REPLY')
        vevent = list(cal.walk('VEVENT'))[0]
        self.assertEqual(str(vevent['UID']), 'evt-123@example.com')
        attendee = vevent['ATTENDEE']
        self.assertIn('bob@test.com', str(attendee))
        self.assertEqual(attendee.params['PARTSTAT'], 'ACCEPTED')

    def test_build_reply_declined(self):
        from workspace.calendar.services.ics_builder import build_reply
        ics_bytes = build_reply(self.event, self.user, 'declined')
        cal = icalendar.Calendar.from_ical(ics_bytes)
        vevent = list(cal.walk('VEVENT'))[0]
        attendee = vevent['ATTENDEE']
        self.assertEqual(attendee.params['PARTSTAT'], 'DECLINED')

    def test_build_reply_contains_organizer(self):
        from workspace.calendar.services.ics_builder import build_reply
        ics_bytes = build_reply(self.event, self.user, 'accepted')
        cal = icalendar.Calendar.from_ical(ics_bytes)
        vevent = list(cal.walk('VEVENT'))[0]
        self.assertIn('alice@example.com', str(vevent['ORGANIZER']))
