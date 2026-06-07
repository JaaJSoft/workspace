from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.calendar.models import Calendar, Event
from workspace.mail.models import (
    MailAccount,
    MailExtraction,
    MailFolder,
    MailMessage,
)

User = get_user_model()


class MailExtractionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="e", password="p")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="e@x.com",
            imap_host="x",
            imap_port=993,
            smtp_host="x",
            smtp_port=587,
        )
        self.folder = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            folder_type="inbox",
        )
        self.message = MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            imap_uid=1,
            message_id="<m@x>",
            date=datetime.now(UTC),
        )
        self.calendar = Calendar.objects.create(
            owner=self.user,
            name="C",
            color="primary",
        )
        self.event = Event.objects.create(
            calendar=self.calendar,
            owner=self.user,
            title="RDV",
            start=datetime(2026, 6, 1, 14, tzinfo=UTC),
        )

    def test_extraction_round_trip(self):
        ex = MailExtraction.objects.create(
            mail_message=self.message,
            kind=MailExtraction.Kind.EVENT,
            target=self.event,
            confidence="high",
            model_used="test-model",
            raw_output={"reasoning": "concert ticket"},
        )
        ex.refresh_from_db()
        self.assertEqual(ex.target, self.event)
        self.assertEqual(ex.kind, "event")
        self.assertEqual(ex.status, "detected")

    def test_status_dismissed(self):
        ex = MailExtraction.objects.create(
            mail_message=self.message,
            kind=MailExtraction.Kind.EVENT,
            target=self.event,
            status=MailExtraction.Status.DISMISSED,
        )
        self.assertEqual(ex.status, "dismissed")

    def test_target_nullable_after_event_deletion(self):
        """When the underlying Event is deleted, GFK lookup returns None
        but target_object_id is preserved as a dangling UUID. This makes
        the extraction row a faithful audit record even after the target
        is gone."""
        ex = MailExtraction.objects.create(
            mail_message=self.message,
            kind=MailExtraction.Kind.EVENT,
            target=self.event,
        )
        self.event.delete()
        ex.refresh_from_db()
        self.assertIsNone(ex.target)
        self.assertIsNotNone(ex.target_object_id)

    def test_cascade_when_mail_message_deleted(self):
        ex_pk = MailExtraction.objects.create(
            mail_message=self.message,
            kind=MailExtraction.Kind.EVENT,
            target=self.event,
        ).pk
        self.message.delete()
        self.assertFalse(MailExtraction.objects.filter(pk=ex_pk).exists())

    def test_one_message_can_have_multiple_extractions(self):
        e2 = Event.objects.create(
            calendar=self.calendar,
            owner=self.user,
            title="Train",
            start=datetime(2026, 6, 2, 9, tzinfo=UTC),
        )
        MailExtraction.objects.create(
            mail_message=self.message,
            kind=MailExtraction.Kind.EVENT,
            target=self.event,
        )
        MailExtraction.objects.create(
            mail_message=self.message,
            kind=MailExtraction.Kind.EVENT,
            target=e2,
        )
        self.assertEqual(self.message.extractions.count(), 2)
