"""Tests for ai.purge_ai_tasks (workspace.ai.tasks.housekeeping)."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.ai.models import AITask
from workspace.ai.tasks.housekeeping import purge_ai_tasks

User = get_user_model()


@override_settings(AI_TASK_RETENTION_DAYS=30)
class PurgeAiTasksTests(TestCase):
    """Behavioural tests for the periodic AITask purge."""

    def setUp(self):
        self.user = User.objects.create_user(username="purgeuser", password="pw")
        self.now = timezone.now()
        self.old = self.now - timedelta(days=60)  # well past retention

    def _make_task(self, *, status, completed_at, created_at=None):
        task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.SUMMARIZE,
            status=status,
            input_data={},
        )
        # ``created_at`` is auto_now_add; force it after creation.
        update_fields = ["completed_at"]
        task.completed_at = completed_at
        if created_at is not None:
            task.created_at = created_at
            update_fields.append("created_at")
        AITask.objects.filter(pk=task.pk).update(
            **{f: getattr(task, f) for f in update_fields}
        )
        task.refresh_from_db()
        return task

    def test_purges_old_completed_task(self):
        old_done = self._make_task(
            status=AITask.Status.COMPLETED,
            completed_at=self.old,
            created_at=self.old,
        )
        result = purge_ai_tasks()
        self.assertEqual(result["deleted"], 1)
        self.assertFalse(AITask.objects.filter(pk=old_done.pk).exists())

    def test_purges_old_failed_task(self):
        old_failed = self._make_task(
            status=AITask.Status.FAILED,
            completed_at=self.old,
            created_at=self.old,
        )
        result = purge_ai_tasks()
        self.assertEqual(result["deleted"], 1)
        self.assertFalse(AITask.objects.filter(pk=old_failed.pk).exists())

    def test_keeps_old_processing_task(self):
        """Regression: a long-running PROCESSING task created more than the
        retention window ago must NOT be deleted - it is still in flight."""
        in_flight = self._make_task(
            status=AITask.Status.PROCESSING,
            completed_at=None,
            created_at=self.old,
        )
        result = purge_ai_tasks()
        self.assertEqual(result["deleted"], 0)
        self.assertTrue(AITask.objects.filter(pk=in_flight.pk).exists())

    def test_keeps_old_pending_task(self):
        """Regression: a PENDING task that has not been picked up yet must
        not be silently deleted just because it sat in the queue too long."""
        queued = self._make_task(
            status=AITask.Status.PENDING,
            completed_at=None,
            created_at=self.old,
        )
        result = purge_ai_tasks()
        self.assertEqual(result["deleted"], 0)
        self.assertTrue(AITask.objects.filter(pk=queued.pk).exists())

    def test_keeps_recently_completed_task(self):
        recent = self._make_task(
            status=AITask.Status.COMPLETED,
            completed_at=self.now - timedelta(days=5),
        )
        result = purge_ai_tasks()
        self.assertEqual(result["deleted"], 0)
        self.assertTrue(AITask.objects.filter(pk=recent.pk).exists())

    def test_purges_only_terminal_old_tasks_in_mixed_set(self):
        """End-to-end: in a queryset that mixes terminal/non-terminal and
        old/new rows, only the terminal-and-old ones disappear."""
        old_done = self._make_task(
            status=AITask.Status.COMPLETED,
            completed_at=self.old,
            created_at=self.old,
        )
        old_failed = self._make_task(
            status=AITask.Status.FAILED,
            completed_at=self.old,
            created_at=self.old,
        )
        old_running = self._make_task(
            status=AITask.Status.PROCESSING,
            completed_at=None,
            created_at=self.old,
        )
        recent_done = self._make_task(
            status=AITask.Status.COMPLETED,
            completed_at=self.now,
        )

        result = purge_ai_tasks()
        self.assertEqual(result["deleted"], 2)

        surviving = set(AITask.objects.values_list("pk", flat=True))
        self.assertIn(old_running.pk, surviving)
        self.assertIn(recent_done.pk, surviving)
        self.assertNotIn(old_done.pk, surviving)
        self.assertNotIn(old_failed.pk, surviving)
