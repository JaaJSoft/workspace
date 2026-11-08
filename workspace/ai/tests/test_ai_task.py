from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from workspace.ai.harness.model import ModelResponse
from workspace.ai.models import AITask, BotProfile
from workspace.ai.services.ai_task import failed_task_count
from workspace.ai.services.responses import handle_generation_error, post_bot_message
from workspace.ai.tasks.editor import editor_action
from workspace.chat.models import Conversation, ConversationMember, Message

User = get_user_model()


class FailedTaskCountTests(TestCase):
    def test_counts_tasks_that_failed_inside_the_window_only(self):
        user = User.objects.create_user(username="counter", password="pw")
        AITask.objects.create(
            owner=user,
            task_type="chat",
            status=AITask.Status.FAILED,
            completed_at=timezone.now(),
        )
        # Failed long ago: outside the window even though the row still exists.
        AITask.objects.create(
            owner=user,
            task_type="chat",
            status=AITask.Status.FAILED,
            completed_at=timezone.now() - timedelta(hours=25),
        )
        AITask.objects.create(
            owner=user,
            task_type="chat",
            status=AITask.Status.COMPLETED,
            completed_at=timezone.now(),
        )

        since = timezone.now() - timedelta(hours=24)
        self.assertEqual(failed_task_count(since), 1)


class RedeliveredTaskTests(TestCase):
    """A Celery redelivery runs the job again on a row that already moved on."""

    SECOND_ANSWER = {
        "content": "second answer",
        "model": "m",
        "prompt_tokens": 1,
        "completion_tokens": 1,
    }

    def setUp(self):
        self.user = User.objects.create_user(username="redelivered", password="pw")

    def _task(self, status, **fields):
        return AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.EDITOR,
            status=status,
            input_data={"action": "improve", "content": "hello"},
            **fields,
        )

    def test_a_completed_task_is_not_run_again(self):
        done_at = timezone.now() - timedelta(minutes=5)
        task = self._task(
            AITask.Status.COMPLETED, result="first answer", completed_at=done_at
        )

        with patch(
            "workspace.ai.tasks.editor.call_llm", return_value=self.SECOND_ANSWER
        ) as call_llm:
            outcome = editor_action(str(task.uuid))

        call_llm.assert_not_called()
        self.assertEqual(outcome["status"], "skipped")
        task.refresh_from_db()
        self.assertEqual(task.status, AITask.Status.COMPLETED)
        self.assertEqual(task.result, "first answer")
        self.assertEqual(task.completed_at, done_at)

    def test_a_task_already_running_is_not_run_twice(self):
        task = self._task(AITask.Status.PROCESSING)

        with patch(
            "workspace.ai.tasks.editor.call_llm", return_value=self.SECOND_ANSWER
        ) as call_llm:
            editor_action(str(task.uuid))

        call_llm.assert_not_called()
        task.refresh_from_db()
        self.assertEqual(task.status, AITask.Status.PROCESSING)


@patch("workspace.notifications.tasks.send_push_notification.delay")
class CancelledReplyTests(APITestCase):
    """The user's cancel wins over a reply that finishes after it."""

    def setUp(self):
        self.user = User.objects.create_user(username="canceller", password="pw")
        self.bot = User.objects.create_user(username="cancelbot", password="pw")
        BotProfile.objects.create(user=self.bot)
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.bot)
        self.ai_task = AITask.objects.create(
            owner=self.bot,
            task_type=AITask.TaskType.CHAT,
            status=AITask.Status.PROCESSING,
            input_data={"conversation_id": str(self.conv.pk)},
        )
        self.client.force_authenticate(self.user)

    def tearDown(self):
        cache.clear()

    def _cancel(self):
        response = self.client.post(
            f"/api/v1/chat/conversations/{self.conv.pk}/bot-cancel"
        )
        self.assertEqual(response.status_code, 200)

    def _bot_messages(self):
        return Message.objects.filter(conversation=self.conv, author=self.bot)

    def test_a_reply_finished_after_the_cancel_is_not_posted(self, mock_push):
        self._cancel()

        posted = post_bot_message(
            self.conv,
            self.bot,
            ModelResponse(content="Too late", model="m"),
            {},
            self.ai_task,
        )

        self.assertIsNone(posted)
        self.assertFalse(self._bot_messages().exists())
        self.ai_task.refresh_from_db()
        self.assertEqual(self.ai_task.status, AITask.Status.FAILED)
        self.assertEqual(self.ai_task.error, "Cancelled by user")
        self.assertEqual(self.ai_task.result, "")

    def test_a_failure_after_the_cancel_keeps_the_cancel(self, mock_push):
        self._cancel()

        handle_generation_error(
            self.conv, self.bot, self.ai_task, RuntimeError("backend down")
        )

        self.assertFalse(self._bot_messages().exists())
        self.ai_task.refresh_from_db()
        self.assertEqual(self.ai_task.status, AITask.Status.FAILED)
        self.assertEqual(self.ai_task.error, "Cancelled by user")
