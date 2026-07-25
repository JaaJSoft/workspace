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
        "content": json.dumps(events),
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

    @patch("workspace.ai.tasks.calendar.call_llm")
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

    @patch("workspace.ai.tasks.calendar.call_llm")
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

    @patch("workspace.ai.tasks.calendar.call_llm")
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

    @patch("workspace.ai.tasks.calendar.call_llm")
    def test_empty_array_creates_nothing(self, mock_llm):
        mock_llm.return_value = _llm_payload([])
        from workspace.ai.tasks.calendar import extract_from_mail_messages

        extract_from_mail_messages(str(self.ai_task.uuid))

        self.assertEqual(MailExtraction.objects.count(), 0)

    @patch("workspace.ai.tasks.calendar.call_llm")
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

    @patch("workspace.ai.tasks.calendar.call_llm")
    @override_settings(AI_EXTRACT_MODEL="custom-extract-model", AI_MODEL="other")
    def test_uses_ai_extract_model_when_configured(self, mock_llm):
        """AI_EXTRACT_MODEL takes precedence over AI_MODEL when set."""
        mock_llm.return_value = _llm_payload([])
        from workspace.ai.tasks.calendar import extract_from_mail_messages

        extract_from_mail_messages(str(self.ai_task.uuid))

        self.assertEqual(mock_llm.call_args.kwargs.get("model"), "custom-extract-model")

    @patch("workspace.ai.tasks.calendar.call_llm")
    @override_settings(AI_EXTRACT_MODEL="", AI_MODEL="fallback-model")
    def test_falls_back_to_ai_model_when_extract_model_empty(self, mock_llm):
        """Empty AI_EXTRACT_MODEL falls through to AI_MODEL (backward compat)."""
        mock_llm.return_value = _llm_payload([])
        from workspace.ai.tasks.calendar import extract_from_mail_messages

        extract_from_mail_messages(str(self.ai_task.uuid))

        self.assertEqual(mock_llm.call_args.kwargs.get("model"), "fallback-model")

    @patch("workspace.ai.tasks.calendar.call_llm")
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
