from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.calendar.models import Event
from workspace.calendar.services.event_creation import (
    create_event_from_payload,
    get_or_create_invitation_calendar,
)
from workspace.mail.models import MailAccount, MailFolder, MailMessage

User = get_user_model()


class CreateEventFromPayloadTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="c", password="p")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="c@x.com",
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

    def _payload(self, **overrides):
        base = {
            "title": "Lunch",
            "start": datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
            "end": datetime(2026, 6, 1, 13, 0, tzinfo=UTC),
            "all_day": False,
            "location": "Cafe",
            "description": "",
        }
        base.update(overrides)
        return base

    def test_creates_event_with_payload_fields(self):
        event = create_event_from_payload(
            user=self.user,
            payload=self._payload(),
            source_message=self.message,
        )
        self.assertEqual(event.title, "Lunch")
        self.assertEqual(event.location, "Cafe")
        self.assertEqual(event.owner, self.user)
        self.assertEqual(event.source_message, self.message)

    def test_routes_to_invitation_calendar(self):
        event = create_event_from_payload(
            user=self.user,
            payload=self._payload(),
            source_message=self.message,
        )
        self.assertEqual(event.calendar.mail_account, self.account)

    def test_get_or_create_invitation_calendar_is_idempotent(self):
        first = get_or_create_invitation_calendar(self.account)
        second = get_or_create_invitation_calendar(self.account)
        self.assertEqual(first.pk, second.pk)

    def test_optional_ical_uid_and_organizer(self):
        event = create_event_from_payload(
            user=self.user,
            payload=self._payload(),
            source_message=self.message,
            ical_uid="ABC-123",
            external_organizer="alice@x.com",
        )
        self.assertEqual(event.ical_uid, "ABC-123")
        self.assertEqual(event.external_organizer, "alice@x.com")

    def test_defaults_when_ical_fields_omitted(self):
        event = create_event_from_payload(
            user=self.user,
            payload=self._payload(),
            source_message=self.message,
        )
        self.assertIsNone(event.ical_uid)
        self.assertIsNone(event.external_organizer)
        self.assertEqual(event.ical_sequence, 0)

    def test_source_defaults_to_manual_when_not_set(self):
        event = create_event_from_payload(
            user=self.user,
            payload=self._payload(),
            source_message=self.message,
        )
        self.assertEqual(event.source, Event.Source.MANUAL)

    def test_source_can_be_set_to_llm(self):
        event = create_event_from_payload(
            user=self.user,
            payload=self._payload(),
            source_message=self.message,
            source=Event.Source.LLM,
        )
        self.assertEqual(event.source, Event.Source.LLM)
