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


def _frozen(days=0, hour=12):
    """Freeze ``django.utils.timezone.now`` at *hour* UTC, *days* from today.

    The cron only sends after each recipient's reminder hour (8:00 local by
    default), so tests pin the clock to a deterministic hour instead of
    inheriting the wall clock of the machine running them.
    """
    frozen = (timezone.now() + timedelta(days=days)).replace(
        hour=hour, minute=15, second=0, microsecond=0
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

    def _run_cron(self, days=0, hour=12):
        with _frozen(days=days, hour=hour):
            return notify_due_tasks()

    def _unread(self, task, user):
        return Notification.objects.filter(
            task=task, recipient=user, read_at__isnull=True
        )


class NotifyDueTasksCronTests(NotifyDueTasksMixin, TestCase):
    def test_notifies_assignee_of_task_due_today(self):
        task = self._task(due_days=0)

        self._run_cron()

        notif = self._unread(task, self.member).get()
        self.assertEqual(notif.origin, "projects")
        self.assertEqual(notif.title, "Ship it")
        self.assertIn("Due today", notif.body)
        self.assertEqual(notif.url, f"/projects/{self.project.uuid}?task={task.uuid}")

    def test_overdue_task_says_since_when(self):
        task = self._task(due_days=-3)

        self._run_cron()

        notif = self._unread(task, self.member).get()
        self.assertIn("Overdue since", notif.body)

    def test_skips_future_undated_and_done_tasks(self):
        self._task(due_days=2, title="future")
        create_task(self.project, self.admin, title="undated", assignees=[self.member])
        self._task(due_days=0, title="finished", status=self.done)

        self._run_cron()

        self.assertEqual(Notification.objects.count(), 0)

    def test_skips_unassigned_tasks_and_departed_assignees(self):
        self._task(due_days=0, assignees=[])
        departed_task = self._task(due_days=0, title="orphaned")
        remove_member(self.membership)

        self._run_cron()

        self.assertEqual(Notification.objects.count(), 0)
        self.assertFalse(self._unread(departed_task, self.member).exists())

    def test_rerun_does_not_stack(self):
        task = self._task(due_days=0)

        self._run_cron()
        self._run_cron()

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

        self._run_cron()

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

        self._run_cron()
        Notification.objects.update(read_at=timezone.now())
        self._run_cron()

        self.assertFalse(self._unread(task, self.member).exists())

    def test_unread_due_reminder_upgrades_to_overdue_in_place(self):
        task = self._task(due_days=0)
        self._run_cron()

        self._run_cron(days=1)

        # Same stream, still unread: the overdue reminder merges into the
        # due-today row instead of stacking a second notification.
        notif = self._unread(task, self.member).get()
        self.assertIn("Overdue since", notif.body)

    def test_claim_reminder_has_a_single_winner(self):
        from workspace.projects.tasks import _claim_reminder

        task = self._task(due_days=0)

        # Two runs racing on the same reminder: only the first claim wins,
        # whether the row is fresh or re-armed by a moved due date.
        self.assertTrue(_claim_reminder(task, self.member.pk, "due"))
        self.assertFalse(_claim_reminder(task, self.member.pk, "due"))

        task.due_date = task.due_date + timedelta(days=1)
        self.assertTrue(_claim_reminder(task, self.member.pk, "due"))
        self.assertFalse(_claim_reminder(task, self.member.pk, "due"))

    def test_becoming_overdue_sends_exactly_one_more_reminder(self):
        task = self._task(due_days=0)
        self._run_cron()
        Notification.objects.update(read_at=timezone.now())

        self._run_cron(days=1)
        self._run_cron(days=1)

        notif = self._unread(task, self.member).get()
        self.assertIn("Overdue since", notif.body)

    def test_moved_due_date_rearms_the_reminder(self):
        task = self._task(due_days=0)
        self._run_cron()
        Notification.objects.update(read_at=timezone.now())

        task.due_date = timezone.localdate() + timedelta(days=1)
        task.save(update_fields=["due_date"])
        self._run_cron(days=1)

        notif = self._unread(task, self.member).get()
        self.assertIn("Due today", notif.body)

    def test_due_today_follows_the_recipients_timezone(self):
        task = self._task(due_days=1, assignees=[self.member, self.admin])
        set_setting(self.member, "core", "timezone", "Pacific/Kiritimati")

        # 18:15 UTC is 08:15 the next day in Kiritimati (UTC+14) - the
        # server's tomorrow, just past the default reminder hour.
        self._run_cron(hour=18)

        notif = self._unread(task, self.member).get()
        self.assertIn("Due today", notif.body)
        # The admin (UTC) is still a day early.
        self.assertFalse(self._unread(task, self.admin).exists())

    def test_waits_for_the_reminder_hour(self):
        task = self._task(due_days=0)

        self._run_cron(hour=6)
        self.assertFalse(self._unread(task, self.member).exists())

        self._run_cron(hour=8)
        self.assertTrue(self._unread(task, self.member).exists())

    def test_reminder_hour_setting_is_honored(self):
        task = self._task(due_days=0)
        set_setting(self.member, "projects", "reminder_hour", 10)

        self._run_cron(hour=9)
        self.assertFalse(self._unread(task, self.member).exists())

        self._run_cron(hour=10)
        self.assertTrue(self._unread(task, self.member).exists())

    def test_garbage_reminder_hour_falls_back_to_default(self):
        task = self._task(due_days=0)
        set_setting(self.member, "projects", "reminder_hour", "not an hour")

        self._run_cron(hour=8)

        self.assertTrue(self._unread(task, self.member).exists())


class SettleOnResolutionTests(NotifyDueTasksMixin, TestCase):
    def test_completing_a_task_settles_its_reminders(self):
        task = self._task(due_days=0)
        self._run_cron()

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
        self._run_cron()

        move_tasks(self.project, self.done, [task.uuid], actor=self.admin)

        self.assertFalse(self._unread(task, self.member).exists())


class SettleOnDueDateChangeTests(NotifyDueTasksMixin, APITestCase):
    def _patch_task(self, task, payload):
        self.client.force_authenticate(self.admin)
        url = f"/api/v1/projects/{self.project.uuid}/tasks/{task.uuid}"
        return self.client.patch(url, payload, format="json")

    def test_pushing_due_date_back_settles(self):
        task = self._task(due_days=0)
        self._run_cron()

        tomorrow = timezone.localdate() + timedelta(days=1)
        resp = self._patch_task(task, {"due_date": tomorrow.isoformat()})

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self._unread(task, self.member).exists())

    def test_clearing_due_date_settles(self):
        task = self._task(due_days=-1)
        self._run_cron()

        resp = self._patch_task(task, {"due_date": None})

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self._unread(task, self.member).exists())

    def test_moving_due_date_to_another_past_day_keeps_the_reminder(self):
        task = self._task(due_days=-1)
        self._run_cron()

        yesterday = timezone.localdate() - timedelta(days=2)
        resp = self._patch_task(task, {"due_date": yesterday.isoformat()})

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self._unread(task, self.member).exists())
