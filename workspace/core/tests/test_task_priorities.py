"""Every Celery task of the project declares a priority from the shared scale."""

from unittest.mock import patch

from celery.beat import ScheduleEntry, Scheduler
from django.conf import settings
from django.test import TestCase, override_settings
from kombu.transport.redis import PRIORITY_STEPS

from workspace.celery import app
from workspace.common.task_priority import LOW_PRIORITY, TASK_PRIORITIES


class TaskPriorityTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # What the worker and beat do at startup: autodiscover every tasks
        # module, so the registry holds all of them.
        app.loader.import_default_modules()

    def test_every_project_task_declares_a_priority_from_the_scale(self):
        tasks = {
            name: task
            for name, task in app.tasks.items()
            if task.__module__.startswith("workspace.")
        }
        self.assertTrue(tasks)
        undeclared = sorted(
            name for name, task in tasks.items() if task.priority not in TASK_PRIORITIES
        )
        self.assertEqual(
            undeclared,
            [],
            "Declare @shared_task(priority=...) from workspace.common.task_priority: "
            "a task sent without one gets 0 and runs ahead of every other task.",
        )

    def test_the_scale_uses_the_redis_priority_steps(self):
        """A priority between two steps is floored to the step below, so two
        levels that fall in the same step would silently be one."""
        self.assertLessEqual(set(TASK_PRIORITIES), set(PRIORITY_STEPS))
        self.assertEqual(len(set(TASK_PRIORITIES)), len(TASK_PRIORITIES))

    def test_every_beat_entry_names_a_registered_task(self):
        """Beat sends an unregistered name as a bare message, without the
        priority the task declares."""
        unknown = sorted(
            entry["task"]
            for entry in settings.CELERY_BEAT_SCHEDULE.values()
            if entry["task"] not in app.tasks
        )
        self.assertEqual(unknown, [])

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_beat_sends_a_scheduled_task_with_its_declared_priority(self):
        """Beat entries carry no priority of their own: the task's declaration
        is what reaches the broker."""
        entry = ScheduleEntry(
            name="sync-all-user-files",
            app=app,
            **settings.CELERY_BEAT_SCHEDULE["sync-all-user-files"],
        )
        scheduler = Scheduler(app, lazy=True)

        with patch.object(app, "send_task") as send_task:
            scheduler.apply_async(entry, producer=object())

        send_task.assert_called_once()
        self.assertEqual(send_task.call_args.kwargs["priority"], LOW_PRIORITY)
