"""Tests for the due-task notification cron and the hooks that settle it."""

from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from workspace.notifications.models import Notification
from workspace.projects.services.members import remove_member
from workspace.projects.services.tasks import (
    apply_status_change,
    create_task,
    move_tasks,
)
from workspace.projects.tasks import notify_due_tasks

from .base import ProjectTestMixin


class NotifyDueTasksMixin(ProjectTestMixin):
    def setUp(self):
        super().setUp()
        self.todo = self.project.statuses.get(name="To do")
        self.done = self.project.statuses.get(name="Done")

    def tearDown(self):
        cache.clear()

    def _task(self, due_days=0, assignees=None, **kwargs):
        return create_task(
            self.project,
            self.admin,
            title=kwargs.pop("title", "Ship it"),
            status=kwargs.pop("status", self.todo),
            due_date=timezone.localdate() + timedelta(days=due_days),
            assignees=assignees if assignees is not None else [self.member],
            **kwargs,
        )

    def _unread(self, task, user):
        return Notification.objects.filter(
            task=task, recipient=user, read_at__isnull=True
        )


class NotifyDueTasksCronTests(NotifyDueTasksMixin, TestCase):
    def test_notifies_assignee_of_task_due_today(self):
        task = self._task(due_days=0)

        notify_due_tasks()

        notif = self._unread(task, self.member).get()
        self.assertEqual(notif.origin, "projects")
        self.assertEqual(notif.title, "Ship it")
        self.assertIn("Due today", notif.body)
        self.assertEqual(notif.url, f"/projects/{self.project.uuid}?task={task.uuid}")

    def test_overdue_task_says_since_when(self):
        task = self._task(due_days=-3)

        notify_due_tasks()

        notif = self._unread(task, self.member).get()
        self.assertIn("Overdue since", notif.body)

    def test_skips_future_undated_and_done_tasks(self):
        self._task(due_days=2, title="future")
        create_task(self.project, self.admin, title="undated", assignees=[self.member])
        self._task(due_days=0, title="finished", status=self.done)

        notify_due_tasks()

        self.assertEqual(Notification.objects.count(), 0)

    def test_skips_unassigned_tasks_and_departed_assignees(self):
        self._task(due_days=0, assignees=[])
        departed_task = self._task(due_days=0, title="orphaned")
        remove_member(self.membership)

        notify_due_tasks()

        self.assertEqual(Notification.objects.count(), 0)
        self.assertFalse(self._unread(departed_task, self.member).exists())

    def test_rerun_merges_instead_of_stacking(self):
        task = self._task(due_days=0)

        notify_due_tasks()
        notify_due_tasks()

        self.assertEqual(self._unread(task, self.member).count(), 1)

    def test_read_notification_is_recreated_on_next_run(self):
        task = self._task(due_days=-1)

        notify_due_tasks()
        Notification.objects.update(read_at=timezone.now())
        notify_due_tasks()

        self.assertEqual(self._unread(task, self.member).count(), 1)


class SettleOnResolutionTests(NotifyDueTasksMixin, TestCase):
    def test_completing_a_task_settles_its_reminders(self):
        task = self._task(due_days=0)
        notify_due_tasks()

        task.status = self.done
        apply_status_change(task, actor=self.admin, old_status=self.todo)

        self.assertFalse(self._unread(task, self.member).exists())

    def test_completing_spares_high_priority_rows(self):
        task = self._task(due_days=0)
        mention = Notification.objects.create(
            recipient=self.member,
            origin="projects",
            icon="i",
            title="mention",
            priority="high",
            task=task,
        )

        task.status = self.done
        apply_status_change(task, actor=self.admin, old_status=self.todo)

        mention.refresh_from_db()
        self.assertIsNone(mention.read_at)

    def test_bulk_move_to_done_settles(self):
        task = self._task(due_days=0)
        notify_due_tasks()

        move_tasks(self.project, self.done, [task.uuid], actor=self.admin)

        self.assertFalse(self._unread(task, self.member).exists())


class SettleOnDueDateChangeTests(NotifyDueTasksMixin, APITestCase):
    def _patch_task(self, task, payload):
        self.client.force_authenticate(self.admin)
        url = f"/api/v1/projects/{self.project.uuid}/tasks/{task.uuid}"
        return self.client.patch(url, payload, format="json")

    def test_pushing_due_date_back_settles(self):
        task = self._task(due_days=0)
        notify_due_tasks()

        tomorrow = timezone.localdate() + timedelta(days=1)
        resp = self._patch_task(task, {"due_date": tomorrow.isoformat()})

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self._unread(task, self.member).exists())

    def test_clearing_due_date_settles(self):
        task = self._task(due_days=-1)
        notify_due_tasks()

        resp = self._patch_task(task, {"due_date": None})

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self._unread(task, self.member).exists())

    def test_moving_due_date_to_another_past_day_keeps_the_reminder(self):
        task = self._task(due_days=-1)
        notify_due_tasks()

        yesterday = timezone.localdate() - timedelta(days=2)
        resp = self._patch_task(task, {"due_date": yesterday.isoformat()})

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self._unread(task, self.member).exists())
