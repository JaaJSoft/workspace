from datetime import datetime, timezone

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.calendar.models import Calendar, Event
from workspace.mail.models import (
    MailAccount, MailExtraction, MailFolder, MailMessage,
)

User = get_user_model()


class ExtractionsDeleteTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='d', password='p')
        self.client.force_authenticate(self.user)
        self.account = MailAccount.objects.create(
            owner=self.user, email='d@x.com',
            imap_host='x', imap_port=993, smtp_host='x', smtp_port=587,
        )
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX', folder_type='inbox',
        )
        self.message = MailMessage.objects.create(
            account=self.account, folder=self.folder, imap_uid=1,
            message_id='<m@x>', date=datetime.now(timezone.utc),
        )
        self.calendar = Calendar.objects.create(owner=self.user, name='C', color='primary')
        self.event = Event.objects.create(
            calendar=self.calendar, owner=self.user, title='X',
            start=datetime(2026, 6, 1, 12, tzinfo=timezone.utc),
        )
        self.ex = MailExtraction.objects.create(
            mail_message=self.message,
            kind=MailExtraction.Kind.EVENT,
            target_content_type=ContentType.objects.get_for_model(Event),
            target_object_id=self.event.uuid,
        )

    def test_delete_flips_status_and_removes_event(self):
        url = f'/api/v1/mail/extractions/{self.ex.uuid}'
        resp = self.client.delete(url)
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.ex.refresh_from_db()
        self.assertEqual(self.ex.status, 'dismissed')
        self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())

    def test_delete_is_idempotent(self):
        self.ex.status = MailExtraction.Status.DISMISSED
        self.ex.save()
        self.event.delete()
        url = f'/api/v1/mail/extractions/{self.ex.uuid}'
        resp = self.client.delete(url)
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_other_users_extraction_returns_404(self):
        other = User.objects.create_user(username='o', password='p')
        self.client.force_authenticate(other)
        url = f'/api/v1/mail/extractions/{self.ex.uuid}'
        resp = self.client.delete(url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_unauthenticated_returns_401_or_403(self):
        self.client.force_authenticate(None)
        url = f'/api/v1/mail/extractions/{self.ex.uuid}'
        resp = self.client.delete(url)
        self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
