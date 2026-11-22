import json
import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone as dj_tz

from workspace.ai.models import AITask
from workspace.ai.services import llm
from workspace.ai.tasks.calendar import extract_from_mail_messages
from workspace.calendar.models import Event
from workspace.common.tests.parameter_limit import sqlite_parameter_limit
from workspace.mail.models import (
    MailAccount,
    MailExtraction,
    MailFolder,
    MailMessage,
)
from workspace.users.services.settings import set_setting

from . import agents

User = get_user_model()


def _reporting(events):
    """A model that reads *events* in every mail it is given."""
    return agents.scripted(
        agents.reply(
            json.dumps({"events": events}), input_tokens=100, output_tokens=20
        ),
        repeat=True,
    )


class ExtractFromMailTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ex", password="p")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="ex@x.com",
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
            date=dj_tz.now(),
            subject="Train",
            body_text="Confirmed for tomorrow 8am.",
            from_email="sncf@x.com",
        )
        self.ai_task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.EXTRACT,
            input_data={"message_uuids": [str(self.message.uuid)]},
        )

    def test_creates_event_and_extraction_on_high_confidence(self):
        future = (dj_tz.now() + timedelta(days=1)).isoformat()
        events = [
            {
                "title": "Train Paris-Lyon",
                "start": future,
                "end": None,
                "all_day": False,
                "location": "Gare de Lyon",
                "description": "",
                "confidence": "high",
                "reasoning": "ticket",
            }
        ]
        with _reporting(events):
            extract_from_mail_messages(str(self.ai_task.uuid))

        self.assertEqual(MailExtraction.objects.count(), 1)
        ex = MailExtraction.objects.first()
        self.assertEqual(ex.kind, "event")
        self.assertEqual(ex.confidence, "high")
        self.assertIsNotNone(ex.target)
        self.assertEqual(ex.target.source, "llm")

    def test_takes_more_messages_than_a_statement_binds(self):
        limit = 50
        message_uuids = [str(uuid.uuid4()) for _ in range(limit * 2)]
        message_uuids.append(str(self.message.uuid))
        self.ai_task.input_data = {"message_uuids": message_uuids}
        self.ai_task.save(update_fields=["input_data"])
        with sqlite_parameter_limit(limit), _reporting([]) as script:
            extract_from_mail_messages(str(self.ai_task.uuid))

        self.assertEqual(len(script.requests), 1)
        self.ai_task.refresh_from_db()
        self.assertEqual(self.ai_task.status, AITask.Status.COMPLETED)

    def test_drops_medium_confidence(self):
        future = (dj_tz.now() + timedelta(days=1)).isoformat()
        events = [
            {
                "title": "Maybe coffee",
                "start": future,
                "end": None,
                "all_day": False,
                "location": "",
                "description": "",
                "confidence": "medium",
                "reasoning": "vague",
            }
        ]
        with _reporting(events):
            extract_from_mail_messages(str(self.ai_task.uuid))

        self.assertEqual(MailExtraction.objects.count(), 0)
        self.assertEqual(Event.objects.count(), 0)

    def _extract(self, **event):
        events = [
            {
                "title": "Train",
                "end": None,
                "all_day": False,
                "location": "",
                "description": "",
                "confidence": "high",
                "reasoning": "ticket",
                **event,
            }
        ]
        with _reporting(events):
            extract_from_mail_messages(str(self.ai_task.uuid))

    def test_offsetless_time_is_the_owners_wall_clock(self):
        set_setting(self.user, "core", "timezone", "Europe/Paris")
        self.addCleanup(cache.clear)
        local = (dj_tz.now() + timedelta(days=2)).astimezone(ZoneInfo("Europe/Paris"))
        wall_clock = local.replace(hour=8, minute=0, second=0, microsecond=0)

        self._extract(start=wall_clock.replace(tzinfo=None).isoformat())

        self.assertEqual(Event.objects.get().start, wall_clock)

    def test_todays_all_day_event_is_not_past(self):
        set_setting(self.user, "core", "timezone", "Europe/Paris")
        self.addCleanup(cache.clear)
        paris = ZoneInfo("Europe/Paris")
        today = dj_tz.now().astimezone(paris).date()
        # Local midnight, already behind "now" for most of the day.
        midnight = datetime.combine(today, time.min, tzinfo=paris)

        self._extract(start=midnight.isoformat(), all_day=True)

        self.assertEqual(
            Event.objects.get().start, datetime.combine(today, time.min, tzinfo=UTC)
        )

    def test_drops_past_events(self):
        past = (dj_tz.now() - timedelta(days=1)).isoformat()
        events = [
            {
                "title": "Yesterday RDV",
                "start": past,
                "end": None,
                "all_day": False,
                "location": "",
                "description": "",
                "confidence": "high",
                "reasoning": "old",
            }
        ]
        with _reporting(events):
            extract_from_mail_messages(str(self.ai_task.uuid))

        self.assertEqual(MailExtraction.objects.count(), 0)

    def test_empty_array_creates_nothing(self):
        with _reporting([]):
            extract_from_mail_messages(str(self.ai_task.uuid))

        self.assertEqual(MailExtraction.objects.count(), 0)

    def test_multi_event_creates_multiple_rows(self):
        f1 = (dj_tz.now() + timedelta(days=1)).isoformat()
        f2 = (dj_tz.now() + timedelta(days=2)).isoformat()
        events = [
            {
                "title": "A",
                "start": f1,
                "end": None,
                "all_day": False,
                "location": "",
                "description": "",
                "confidence": "high",
                "reasoning": "r1",
            },
            {
                "title": "B",
                "start": f2,
                "end": None,
                "all_day": False,
                "location": "",
                "description": "",
                "confidence": "high",
                "reasoning": "r2",
            },
        ]
        with _reporting(events):
            extract_from_mail_messages(str(self.ai_task.uuid))

        self.assertEqual(
            MailExtraction.objects.filter(mail_message=self.message).count(), 2
        )

    @override_settings(AI_EXTRACT_MODEL="custom-extract-model", AI_MODEL="other")
    def test_uses_ai_extract_model_when_configured(self):
        """AI_EXTRACT_MODEL takes precedence over AI_MODEL when set."""
        with _reporting([]):
            extract_from_mail_messages(str(self.ai_task.uuid))
            # scripted() stands in for get_model: its call names the model asked.
            llm.get_model.assert_called_once_with("custom-extract-model")

    @override_settings(AI_EXTRACT_MODEL="", AI_MODEL="fallback-model")
    def test_falls_back_to_ai_model_when_extract_model_empty(self):
        """Empty AI_EXTRACT_MODEL falls through to AI_MODEL (backward compat)."""
        with _reporting([]):
            extract_from_mail_messages(str(self.ai_task.uuid))
            llm.get_model.assert_called_once_with("fallback-model")

    def test_malformed_json_skips_message_without_crashing(self):
        # An answer that is not the schema is asked for once more, then given up on.
        with agents.scripted(agents.reply("not json at all"), repeat=True) as script:
            extract_from_mail_messages(str(self.ai_task.uuid))

        self.assertEqual(len(script.requests), 2)

        self.assertEqual(MailExtraction.objects.count(), 0)
        self.ai_task.refresh_from_db()
        self.assertEqual(self.ai_task.status, AITask.Status.COMPLETED)


def _event(title, start, *, all_day=False, confidence="high"):
    return {
        "title": title,
        "start": start.isoformat(),
        "end": None,
        "all_day": all_day,
        "location": "",
        "description": "",
        "confidence": confidence,
        "reasoning": "r",
    }


class ExtractDeduplicationTests(TestCase):
    """Every mail of a thread re-sends the whole thread to the model, which
    names the same event differently each time: the title is no identity."""

    def setUp(self):
        self.user = User.objects.create_user(username="dup", password="p")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="dup@x.com",
            imap_host="x",
            imap_port=993,
            smtp_host="x",
            smtp_port=587,
        )
        self.folder = MailFolder.objects.create(
            account=self.account, name="INBOX", folder_type="inbox"
        )
        self.first = self._message(1, "<a@x>", "Dentist")
        self.start = (dj_tz.now() + timedelta(days=3)).replace(microsecond=0)

    def tearDown(self):
        cache.clear()

    def _message(self, uid, message_id, subject, in_reply_to=""):
        return MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            imap_uid=uid,
            message_id=message_id,
            in_reply_to=in_reply_to,
            date=dj_tz.now(),
            subject=subject,
            body_text="See you then.",
            from_email="doc@x.com",
        )

    def _extract(self, message, events):
        task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.EXTRACT,
            input_data={"message_uuids": [str(message.uuid)]},
        )
        with _reporting(events):
            extract_from_mail_messages(str(task.uuid))

    def test_reply_renaming_the_same_event_reuses_it(self):
        reply = self._message(2, "<b@x>", "Re: Dentist", in_reply_to="<a@x>")

        self._extract(self.first, [_event("Dentist", self.start)])
        self._extract(reply, [_event("Dental appointment - Dr Martin", self.start)])

        event = Event.objects.get()
        self.assertEqual(event.title, "Dentist")
        reply_extraction = MailExtraction.objects.get(mail_message=reply)
        self.assertEqual(reply_extraction.target_object_id, event.uuid)

    def test_unthreaded_reminder_reuses_the_event(self):
        reminder = self._message(2, "<reminder@x>", "Reminder: your appointment")

        self._extract(self.first, [_event("Dentist", self.start)])
        self._extract(reminder, [_event("Appointment", self.start)])

        self.assertEqual(Event.objects.count(), 1)

    def test_event_from_an_invitation_is_not_extracted_again(self):
        from workspace.calendar.services.event_creation import (
            create_event_from_payload,
        )

        create_event_from_payload(
            user=self.user,
            payload={"title": "Dentist (invite)", "start": self.start},
            source_message=self.first,
            source=Event.Source.ICS,
            ical_uid="uid-1",
        )

        self._extract(self.first, [_event("Dentist", self.start)])

        ics_event = Event.objects.get()
        self.assertTrue(
            MailExtraction.objects.filter(
                mail_message=self.first, target_object_id=ics_event.uuid
            ).exists()
        )

    def test_all_day_invitation_matches_a_local_midnight_west_of_utc(self):
        from workspace.calendar.services.event_creation import (
            create_event_from_payload,
        )

        day = (self.start + timedelta(days=1)).date()
        create_event_from_payload(
            user=self.user,
            payload={
                "title": "Offsite (invite)",
                "start": datetime.combine(day, time.min, tzinfo=UTC),
                "all_day": True,
            },
            source_message=self.first,
            source=Event.Source.ICS,
            ical_uid="uid-2",
        )
        set_setting(self.user, "core", "timezone", "America/New_York")
        new_york_midnight = datetime.combine(
            day, time.min, tzinfo=ZoneInfo("America/New_York")
        )

        self._extract(self.first, [_event("Offsite", new_york_midnight, all_day=True)])

        self.assertEqual(Event.objects.count(), 1)

    def test_event_the_user_deleted_is_not_recreated_by_a_reply(self):
        reply = self._message(2, "<b@x>", "Re: Dentist", in_reply_to="<a@x>")

        self._extract(self.first, [_event("Dentist", self.start)])
        Event.objects.all().delete()
        self._extract(reply, [_event("Dentist appointment", self.start)])

        self.assertEqual(Event.objects.count(), 0)
        self.assertFalse(MailExtraction.objects.filter(mail_message=reply).exists())

    def test_all_day_event_on_the_same_day_is_reused(self):
        reply = self._message(2, "<b@x>", "Re: Dentist", in_reply_to="<a@x>")
        midnight = self.start.replace(hour=0, minute=0, second=0)

        self._extract(self.first, [_event("Trip", midnight, all_day=True)])
        self._extract(
            reply,
            [_event("Holiday trip", midnight + timedelta(hours=2), all_day=True)],
        )

        self.assertEqual(Event.objects.count(), 1)

    def test_rerun_on_the_same_message_adds_no_extraction(self):
        self._extract(self.first, [_event("Dentist", self.start)])
        self._extract(self.first, [_event("Dentist", self.start)])

        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(MailExtraction.objects.count(), 1)

    def test_event_at_another_time_is_still_created(self):
        reply = self._message(2, "<b@x>", "Re: Dentist", in_reply_to="<a@x>")

        self._extract(self.first, [_event("Dentist", self.start)])
        self._extract(
            reply,
            [_event("Follow-up visit", self.start + timedelta(days=7))],
        )

        self.assertEqual(Event.objects.count(), 2)

    def test_all_day_event_is_stored_on_the_day_the_mail_names(self):
        """All-day events live at UTC midnight of their day: a local midnight
        east of UTC is the previous UTC day, and would show a day early."""
        day = (self.start + timedelta(days=1)).date()
        paris_midnight = datetime.combine(
            day, time.min, tzinfo=ZoneInfo("Europe/Paris")
        )

        self._extract(
            self.first,
            [
                {
                    **_event("Trip", paris_midnight, all_day=True),
                    "end": (paris_midnight + timedelta(days=2)).isoformat(),
                }
            ],
        )

        event = Event.objects.get()
        self.assertEqual(event.start, datetime.combine(day, time.min, tzinfo=UTC))
        self.assertEqual(
            event.end,
            datetime.combine(day + timedelta(days=2), time.min, tzinfo=UTC),
        )
