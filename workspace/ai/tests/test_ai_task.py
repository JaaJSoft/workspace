from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.ai.models import AITask
from workspace.ai.services.ai_task import failed_task_count

User = get_user_model()


class FailedTaskCountTests(TestCase):
    def test_counts_failed_tasks_inside_the_window_only(self):
        user = User.objects.create_user(username="counter", password="pw")
        AITask.objects.create(owner=user, task_type="chat", status=AITask.Status.FAILED)
        old = AITask.objects.create(
            owner=user, task_type="chat", status=AITask.Status.FAILED
        )
        AITask.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(hours=25)
        )
        AITask.objects.create(
            owner=user, task_type="chat", status=AITask.Status.COMPLETED
        )

        since = timezone.now() - timedelta(hours=24)
        self.assertEqual(failed_task_count(since), 1)
