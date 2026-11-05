from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.ai.models import AITask
from workspace.ai.services.chat_generations import active_chat_tasks

User = get_user_model()


class ActiveChatTasksTests(TestCase):
    def setUp(self):
        self.bot = User.objects.create_user(username="bot_gen", password="pw")

    def _task(self, status=AITask.Status.PROCESSING, task_type=AITask.TaskType.CHAT):
        return AITask.objects.create(
            owner=self.bot,
            task_type=task_type,
            status=status,
            input_data={"conversation_id": "c"},
        )

    def test_queued_and_running_replies_are_active(self):
        pending = self._task(AITask.Status.PENDING)
        running = self._task(AITask.Status.PROCESSING)

        self.assertEqual(set(active_chat_tasks()), {pending, running})

    def test_finished_replies_and_other_task_types_are_not(self):
        self._task(AITask.Status.COMPLETED)
        self._task(AITask.Status.FAILED)
        self._task(task_type=AITask.TaskType.SUMMARIZE)

        self.assertFalse(active_chat_tasks().exists())

    def test_a_task_older_than_any_run_can_last_is_dead(self):
        # A worker killed at its hard limit never closes its row; counted
        # as running, it would keep the typing bubble up and the composer
        # locked for good.
        task = self._task()
        later = task.created_at + timedelta(seconds=settings.CELERY_TASK_TIME_LIMIT + 1)

        with patch("django.utils.timezone.now", return_value=later):
            self.assertFalse(active_chat_tasks().exists())

        just_in_time = task.created_at + timedelta(
            seconds=settings.CELERY_TASK_TIME_LIMIT - 1
        )
        with patch("django.utils.timezone.now", return_value=just_in_time):
            self.assertTrue(active_chat_tasks().exists())
