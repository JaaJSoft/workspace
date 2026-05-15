from datetime import datetime, timezone

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from workspace.calendar.models import Calendar, Event
from workspace.mail.models import MailAccount, MailExtraction, MailFolder, MailMessage
from workspace.mail.serializers import MailMessageDetailSerializer

User = get_user_model()


class MailMessageDetailExtractionsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='s', password='p')
        self.account = MailAccount.objects.create(
            owner=self.user, email='s@x.com',
            imap_host='x', imap_port=993, smtp_host='x', smtp_port=587,
        )
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX', folder_type='inbox',
        )
        self.message = MailMessage.objects.create(
            account=self.account, folder=self.folder, imap_uid=1,
            message_id='<m@x>', date=datetime.now(timezone.utc),
        )
        self.calendar = Calendar.objects.create(
            owner=self.user, name='C', color='primary',
        )
        self.event = Event.objects.create(
            calendar=self.calendar, owner=self.user, title='X',
            start=datetime(2026, 6, 1, 12, tzinfo=timezone.utc),
        )

    def test_detail_includes_extractions(self):
        MailExtraction.objects.create(
            mail_message=self.message,
            kind=MailExtraction.Kind.EVENT,
            target_content_type=ContentType.objects.get_for_model(Event),
            target_object_id=self.event.uuid,
        )

        data = MailMessageDetailSerializer(self.message).data
        self.assertEqual(len(data['extractions']), 1)
        self.assertEqual(data['extractions'][0]['kind'], 'event')
        self.assertEqual(data['extractions'][0]['target']['title'], 'X')

    def test_dismissed_extractions_excluded_from_detail(self):
        MailExtraction.objects.create(
            mail_message=self.message,
            kind=MailExtraction.Kind.EVENT,
            status=MailExtraction.Status.DISMISSED,
            target_content_type=ContentType.objects.get_for_model(Event),
            target_object_id=self.event.uuid,
        )
        data = MailMessageDetailSerializer(self.message).data
        self.assertEqual(data['extractions'], [])
