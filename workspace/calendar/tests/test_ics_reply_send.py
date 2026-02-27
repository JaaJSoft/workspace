from datetime import datetime, timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.calendar.models import Calendar, Event, EventMember
from workspace.mail.models import MailAccount, MailFolder, MailMessage

User = get_user_model()


class EventRespondReplyTest(APITestCase):
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
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX', display_name='Inbox', folder_type='inbox',
        )
        self.calendar = Calendar.objects.create(
            name='Invitations', owner=self.user, mail_account=self.account,
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
            organizer_email='alice@external.com',
            source_message=self.mail_msg,
        )
        EventMember.objects.create(event=self.event, user=self.user)

    @patch('workspace.calendar.tasks.send_ics_reply.delay')
    def test_accept_sends_reply_email(self, mock_send):
        self.client.force_authenticate(self.user)
        url = f'/api/v1/calendar/events/{self.event.pk}/respond'
        resp = self.client.post(url, {'status': 'accepted'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        mock_send.assert_called_once_with(str(self.event.pk), self.user.id, 'accepted')

    @patch('workspace.calendar.tasks.send_ics_reply.delay')
    def test_decline_sends_reply_email(self, mock_send):
        self.client.force_authenticate(self.user)
        url = f'/api/v1/calendar/events/{self.event.pk}/respond'
        resp = self.client.post(url, {'status': 'declined'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        mock_send.assert_called_once_with(str(self.event.pk), self.user.id, 'declined')

    @patch('workspace.calendar.tasks.send_ics_reply.delay')
    def test_no_reply_for_non_email_event(self, mock_send):
        self.event.organizer_email = None
        self.event.source_message = None
        self.event.save()
        self.client.force_authenticate(self.user)
        url = f'/api/v1/calendar/events/{self.event.pk}/respond'
        resp = self.client.post(url, {'status': 'accepted'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        mock_send.assert_not_called()
