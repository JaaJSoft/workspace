"""Tests for the due-task notification cron and the hooks that settle it."""

from datetime import timedelta
from unittest import mock

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
from workspace.users.services.settings import set_setting

from .base import ProjectTestMixin


def _time_travel(delta_or_now):
    """Freeze ``django.utils.timezone.now`` (and everything built on it)."""
    frozen = (
        timezone.now() + delta_or_now
        if isinstance(delta_or_now, timedelta)
        else delta_or_now
    )
    return mock.patch("django.utils.timezone.now", return_value=frozen)


class NotifyDueTasksMixin(ProjectTestMixin):
    def setUp(self):
        super().setUp()
        self.todo = self.project.statuses.get(name="To do")
        self.done = self.project.statuses.get(name="Done")

    def tearDown(self):
        cache.clear()

    def _task(self, due_days=0, assignees=None, **kwargs):
        task = create_task(
            self.project,
            self.admin,
            title=kwargs.pop("title", "Ship it"),
            status=kwargs.pop("status", self.todo),
            due_date=timezone.localdate() + timedelta(days=due_days),
            assignees=assignees if assignees is not None else [self.member],
            **kwargs,
        )
        # create_task notifies the new assignees; these tests target the
        # due-date reminders only.
        Notification.objects.all().delete()
        return task

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

    def test_rerun_does_not_stack(self):
        task = self._task(due_days=0)

        notify_due_tasks()
        notify_due_tasks()

        self.assertEqual(self._unread(task, self.member).count(), 1)

    def test_reminder_does_not_repurpose_an_existing_task_notification(self):
        task = self._task(due_days=0)
        mention = Notification.objects.create(
            recipient=self.member,
            origin="projects",
            icon="i",
            title="admin1 mentioned you",
            body="see my comment",
            priority="high",
            task=task,
        )

        notify_due_tasks()

        mention.refresh_from_db()
        self.assertEqual(mention.title, "admin1 mentioned you")
        self.assertEqual(mention.body, "see my comment")
        # The reminder lands on its own row instead of merging into the
        # mention - notify_stream only merges within a stream.
        self.assertEqual(self._unread(task, self.member).count(), 2)
        reminder = self._unread(task, self.member).exclude(pk=mention.pk).get()
        self.assertEqual(reminder.stream, "reminder")

    def test_read_reminder_is_not_resent(self):
        task = self._task(due_days=-1)

        notify_due_tasks()
        Notification.objects.update(read_at=timezone.now())
        notify_due_tasks()

        self.assertFalse(self._unread(task, self.member).exists())

    def test_becoming_overdue_sends_exactly_one_more_reminder(self):
        task = self._task(due_days=0)
        notify_due_tasks()
        Notification.objects.update(read_at=timezone.now())

        with _time_travel(timedelta(days=1)):
            notify_due_tasks()
            notify_due_tasks()

        notif = self._unread(task, self.member).get()
        self.assertIn("Overdue since", notif.body)

    def test_moved_due_date_rearms_the_reminder(self):
        task = self._task(due_days=0)
        notify_due_tasks()
        Notification.objects.update(read_at=timezone.now())

        task.due_date = timezone.localdate() + timedelta(days=1)
        task.save(update_fields=["due_date"])
        with _time_travel(timedelta(days=1)):
            notify_due_tasks()

        notif = self._unread(task, self.member).get()
        self.assertIn("Due today", notif.body)

    def test_due_today_follows_the_recipients_timezone(self):
        task = self._task(due_days=1, assignees=[self.member, self.admin])
        # UTC+14: at 15:00 UTC the local date is already the server's tomorrow.
        set_setting(self.member, "core", "timezone", "Pacific/Kiritimati")

        afternoon_utc = timezone.now().replace(hour=15, minute=0)
        with _time_travel(afternoon_utc):
            notify_due_tasks()

        notif = self._unread(task, self.member).get()
        self.assertIn("Due today", notif.body)
        # The admin (UTC) is still a day early.
        self.assertFalse(self._unread(task, self.admin).exists())


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
