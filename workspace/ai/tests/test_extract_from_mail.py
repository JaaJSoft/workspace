from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone as dj_tz

from workspace.ai.models import AITask
from workspace.calendar.models import Event
from workspace.mail.models import (
    MailAccount,
    MailExtraction,
    MailFolder,
    MailMessage,
)

User = get_user_model()


def _llm_payload(events):
    """Mirror call_llm()'s return shape, with events as a JSON string."""
    import json

    return {
        "content": json.dumps({"events": events}),
        "tool_calls": [],
        "model": "test-model",
        "prompt_tokens": 100,
        "completion_tokens": 20,
    }


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

    @patch("workspace.ai.services.llm.call_llm")
    def test_creates_event_and_extraction_on_high_confidence(self, mock_llm):
        future = (dj_tz.now() + timedelta(days=1)).isoformat()
        mock_llm.return_value = _llm_payload(
            [
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
        )
        from workspace.ai.tasks.calendar import extract_from_mail_messages

        extract_from_mail_messages(str(self.ai_task.uuid))

        self.assertEqual(MailExtraction.objects.count(), 1)
        ex = MailExtraction.objects.first()
        self.assertEqual(ex.kind, "event")
        self.assertEqual(ex.confidence, "high")
        self.assertIsNotNone(ex.target)
        self.assertEqual(ex.target.source, "llm")

    @patch("workspace.ai.services.llm.call_llm")
    def test_drops_medium_confidence(self, mock_llm):
        future = (dj_tz.now() + timedelta(days=1)).isoformat()
        mock_llm.return_value = _llm_payload(
            [
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
        )
        from workspace.ai.tasks.calendar import extract_from_mail_messages

        extract_from_mail_messages(str(self.ai_task.uuid))

        self.assertEqual(MailExtraction.objects.count(), 0)
        self.assertEqual(Event.objects.count(), 0)

    @patch("workspace.ai.services.llm.call_llm")
    def test_drops_past_events(self, mock_llm):
        past = (dj_tz.now() - timedelta(days=1)).isoformat()
        mock_llm.return_value = _llm_payload(
            [
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
        )
        from workspace.ai.tasks.calendar import extract_from_mail_messages

        extract_from_mail_messages(str(self.ai_task.uuid))

        self.assertEqual(MailExtraction.objects.count(), 0)

    @patch("workspace.ai.services.llm.call_llm")
    def test_empty_array_creates_nothing(self, mock_llm):
        mock_llm.return_value = _llm_payload([])
        from workspace.ai.tasks.calendar import extract_from_mail_messages

        extract_from_mail_messages(str(self.ai_task.uuid))

        self.assertEqual(MailExtraction.objects.count(), 0)

    @patch("workspace.ai.services.llm.call_llm")
    def test_multi_event_creates_multiple_rows(self, mock_llm):
        f1 = (dj_tz.now() + timedelta(days=1)).isoformat()
        f2 = (dj_tz.now() + timedelta(days=2)).isoformat()
        mock_llm.return_value = _llm_payload(
            [
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
        )
        from workspace.ai.tasks.calendar import extract_from_mail_messages

        extract_from_mail_messages(str(self.ai_task.uuid))

        self.assertEqual(
            MailExtraction.objects.filter(mail_message=self.message).count(), 2
        )

    @patch("workspace.ai.services.llm.call_llm")
    @override_settings(AI_EXTRACT_MODEL="custom-extract-model", AI_MODEL="other")
    def test_uses_ai_extract_model_when_configured(self, mock_llm):
        """AI_EXTRACT_MODEL takes precedence over AI_MODEL when set."""
        mock_llm.return_value = _llm_payload([])
        from workspace.ai.tasks.calendar import extract_from_mail_messages

        extract_from_mail_messages(str(self.ai_task.uuid))

        self.assertEqual(mock_llm.call_args.kwargs.get("model"), "custom-extract-model")

    @patch("workspace.ai.services.llm.call_llm")
    @override_settings(AI_EXTRACT_MODEL="", AI_MODEL="fallback-model")
    def test_falls_back_to_ai_model_when_extract_model_empty(self, mock_llm):
        """Empty AI_EXTRACT_MODEL falls through to AI_MODEL (backward compat)."""
        mock_llm.return_value = _llm_payload([])
        from workspace.ai.tasks.calendar import extract_from_mail_messages

        extract_from_mail_messages(str(self.ai_task.uuid))

        self.assertEqual(mock_llm.call_args.kwargs.get("model"), "fallback-model")

    @patch("workspace.ai.services.llm.call_llm")
    def test_malformed_json_skips_message_without_crashing(self, mock_llm):
        mock_llm.return_value = {
            "content": "not json at all",
            "tool_calls": [],
            "model": "test",
            "prompt_tokens": 1,
            "completion_tokens": 1,
        }
        from workspace.ai.tasks.calendar import extract_from_mail_messages

        extract_from_mail_messages(str(self.ai_task.uuid))

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

    def _extract(self, message, mock_llm, events):
        from workspace.ai.tasks.calendar import extract_from_mail_messages

        mock_llm.return_value = _llm_payload(events)
        task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.EXTRACT,
            input_data={"message_uuids": [str(message.uuid)]},
        )
        extract_from_mail_messages(str(task.uuid))

    @patch("workspace.ai.services.llm.call_llm")
    def test_reply_renaming_the_same_event_reuses_it(self, mock_llm):
        reply = self._message(2, "<b@x>", "Re: Dentist", in_reply_to="<a@x>")

        self._extract(self.first, mock_llm, [_event("Dentist", self.start)])
        self._extract(
            reply, mock_llm, [_event("Dental appointment - Dr Martin", self.start)]
        )

        event = Event.objects.get()
        self.assertEqual(event.title, "Dentist")
        reply_extraction = MailExtraction.objects.get(mail_message=reply)
        self.assertEqual(reply_extraction.target_object_id, event.uuid)

    @patch("workspace.ai.services.llm.call_llm")
    def test_unthreaded_reminder_reuses_the_event(self, mock_llm):
        reminder = self._message(2, "<reminder@x>", "Reminder: your appointment")

        self._extract(self.first, mock_llm, [_event("Dentist", self.start)])
        self._extract(reminder, mock_llm, [_event("Appointment", self.start)])

        self.assertEqual(Event.objects.count(), 1)

    @patch("workspace.ai.services.llm.call_llm")
    def test_event_from_an_invitation_is_not_extracted_again(self, mock_llm):
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

        self._extract(self.first, mock_llm, [_event("Dentist", self.start)])

        self.assertEqual(Event.objects.count(), 1)

    @patch("workspace.ai.services.llm.call_llm")
    def test_event_the_user_deleted_is_not_recreated_by_a_reply(self, mock_llm):
        reply = self._message(2, "<b@x>", "Re: Dentist", in_reply_to="<a@x>")

        self._extract(self.first, mock_llm, [_event("Dentist", self.start)])
        Event.objects.all().delete()
        self._extract(reply, mock_llm, [_event("Dentist appointment", self.start)])

        self.assertEqual(Event.objects.count(), 0)
        self.assertFalse(MailExtraction.objects.filter(mail_message=reply).exists())

    @patch("workspace.ai.services.llm.call_llm")
    def test_all_day_event_on_the_same_day_is_reused(self, mock_llm):
        reply = self._message(2, "<b@x>", "Re: Dentist", in_reply_to="<a@x>")
        midnight = self.start.replace(hour=0, minute=0, second=0)

        self._extract(self.first, mock_llm, [_event("Trip", midnight, all_day=True)])
        self._extract(
            reply,
            mock_llm,
            [_event("Holiday trip", midnight + timedelta(hours=2), all_day=True)],
        )

        self.assertEqual(Event.objects.count(), 1)

    @patch("workspace.ai.services.llm.call_llm")
    def test_rerun_on_the_same_message_adds_no_extraction(self, mock_llm):
        self._extract(self.first, mock_llm, [_event("Dentist", self.start)])
        self._extract(self.first, mock_llm, [_event("Dentist", self.start)])

        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(MailExtraction.objects.count(), 1)

    @patch("workspace.ai.services.llm.call_llm")
    def test_event_at_another_time_is_still_created(self, mock_llm):
        reply = self._message(2, "<b@x>", "Re: Dentist", in_reply_to="<a@x>")

        self._extract(self.first, mock_llm, [_event("Dentist", self.start)])
        self._extract(
            reply,
            mock_llm,
            [_event("Follow-up visit", self.start + timedelta(days=7))],
        )

        self.assertEqual(Event.objects.count(), 2)
