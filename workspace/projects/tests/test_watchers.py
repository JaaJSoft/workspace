"""Tests for task watchers: explicit watch/mute, auto-watch, fan-out."""

from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from workspace.notifications.models import Notification
from workspace.projects.models import (
    ProjectNotificationLevel,
    TaskReminder,
    TaskWatcher,
)
from workspace.projects.services.comments import notify_comment_added
from workspace.projects.services.tasks import apply_status_change, create_task
from workspace.projects.services.watchers import (
    auto_watch,
    clear_watch_state,
    set_watch_state,
)
from workspace.users.services.settings import set_setting

from .base import ProjectTestMixin


class WatcherTestsBase(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.todo = self.project.statuses.get(name="To do")
        self.done = self.project.statuses.get(name="Done")

    def tearDown(self):
        cache.clear()

    def _task(self, **kwargs):
        task = create_task(
            self.project, self.admin, title=kwargs.pop("title", "Ship it"), **kwargs
        )
        Notification.objects.all().delete()
        return task

    def _unread(self, task, user):
        return Notification.objects.filter(
            task=task, recipient=user, read_at__isnull=True
        )


class WatchStateTests(WatcherTestsBase):
    def test_set_watch_state_creates_then_flips(self):
        task = self._task()

        set_watch_state(task, self.member, muted=False)
        self.assertFalse(TaskWatcher.objects.get(task=task, user=self.member).muted)

        set_watch_state(task, self.member, muted=True)
        self.assertTrue(TaskWatcher.objects.get(task=task, user=self.member).muted)
        self.assertEqual(TaskWatcher.objects.filter(task=task).count(), 1)

    def test_clear_watch_state_is_idempotent(self):
        task = self._task()
        set_watch_state(task, self.member, muted=False)

        clear_watch_state(task, self.member)
        clear_watch_state(task, self.member)

        self.assertFalse(TaskWatcher.objects.filter(task=task).exists())

    def test_auto_watch_respects_the_setting_and_existing_mutes(self):
        task = self._task()
        set_setting(self.admin, "projects", "auto_watch", False)
        set_watch_state(task, self.outsider, muted=True)

        auto_watch(task, [self.admin, self.member, self.outsider])

        self.assertFalse(
            TaskWatcher.objects.filter(task=task, user=self.admin).exists()
        )
        self.assertFalse(TaskWatcher.objects.get(task=task, user=self.member).muted)
        self.assertTrue(TaskWatcher.objects.get(task=task, user=self.outsider).muted)

    def test_assignment_auto_watches_the_assignee(self):
        task = create_task(
            self.project, self.admin, title="Ship it", assignees=[self.member]
        )
        self.assertFalse(TaskWatcher.objects.get(task=task, user=self.member).muted)


class CommentFanOutTests(WatcherTestsBase):
    def test_watcher_gets_comment_notifications(self):
        task = self._task()
        set_watch_state(task, self.member, muted=False)

        notify_comment_added(task, self.admin, "progress?")

        self.assertTrue(self._unread(task, self.member).exists())

    def test_muted_user_is_dropped_from_the_implicit_set(self):
        task = self._task(assignees=[self.member])
        set_watch_state(task, self.member, muted=True)

        notify_comment_added(task, self.admin, "progress?")

        self.assertFalse(self._unread(task, self.member).exists())

    def test_muted_user_still_gets_mentions(self):
        task = self._task(assignees=[self.member])
        set_watch_state(task, self.member, muted=True)

        notify_comment_added(task, self.admin, "ping @member1")

        notif = self._unread(task, self.member).get()
        self.assertIn("mentioned you", notif.title)

    def test_watcher_without_project_access_is_skipped(self):
        task = self._task()
        TaskWatcher.objects.create(task=task, user=self.outsider, muted=False)

        notify_comment_added(task, self.admin, "progress?")

        self.assertFalse(self._unread(task, self.outsider).exists())

    def test_commenting_actor_is_not_notified_even_when_watching(self):
        task = self._task()
        set_watch_state(task, self.admin, muted=False)

        notify_comment_added(task, self.admin, "note to self")

        self.assertFalse(self._unread(task, self.admin).exists())


class StatusChangeTests(WatcherTestsBase):
    def _move(self, task, status, actor):
        task.status = status
        old_status = self.todo if status != self.todo else self.done
        apply_status_change(task, actor=actor, old_status=old_status)

    def test_watcher_is_notified_of_a_move(self):
        task = self._task(status=self.todo)
        set_watch_state(task, self.member, muted=False)

        in_progress = self.project.statuses.get(name="In progress")
        self._move(task, in_progress, self.admin)

        notif = self._unread(task, self.member).get()
        self.assertEqual(notif.stream, "status")
        self.assertIn("moved", notif.title)
        self.assertIn("In progress", notif.title)

    def test_completion_notifies_watchers_after_the_settle(self):
        task = self._task(status=self.todo)
        set_watch_state(task, self.member, muted=False)

        self._move(task, self.done, self.admin)

        notif = self._unread(task, self.member).get()
        self.assertIn("completed", notif.title)

    def test_non_watchers_and_the_actor_are_not_notified(self):
        # admin is the creator but not a watcher; member moves the task.
        task = self._task(status=self.todo)
        set_watch_state(task, self.member, muted=False)

        self._move(task, self.done, self.member)

        self.assertFalse(self._unread(task, self.admin).exists())
        self.assertFalse(self._unread(task, self.member).exists())

    def test_muted_watcher_is_not_notified(self):
        task = self._task(status=self.todo)
        set_watch_state(task, self.member, muted=True)

        self._move(task, self.done, self.admin)

        self.assertFalse(self._unread(task, self.member).exists())

    def test_notification_level_none_suppresses_status_notifications(self):
        task = self._task(status=self.todo)
        set_watch_state(task, self.member, muted=False)
        ProjectNotificationLevel.objects.create(
            project=self.project,
            user=self.member,
            level=ProjectNotificationLevel.Level.NONE,
        )

        self._move(task, self.done, self.admin)

        self.assertFalse(self._unread(task, self.member).exists())


class ReminderTests(WatcherTestsBase):
    def _due_task(self, **kwargs):
        return self._task(status=self.todo, due_date=timezone.localdate(), **kwargs)

    def _run_cron(self):
        from workspace.projects.tasks import notify_due_tasks

        frozen = timezone.now().replace(hour=12, minute=15, second=0, microsecond=0)
        with mock.patch("django.utils.timezone.now", return_value=frozen):
            return notify_due_tasks()

    def test_watcher_gets_a_due_reminder_without_being_assigned(self):
        task = self._due_task(assignees=[self.member])
        set_watch_state(task, self.admin, muted=False)

        sent = self._run_cron()

        self.assertEqual(sent, 2)
        self.assertTrue(self._unread(task, self.admin).exists())
        self.assertTrue(self._unread(task, self.member).exists())

    def test_muted_assignee_gets_no_reminder_and_none_is_claimed(self):
        task = self._due_task(assignees=[self.member])
        set_watch_state(task, self.member, muted=True)

        sent = self._run_cron()

        self.assertEqual(sent, 0)
        self.assertFalse(TaskReminder.objects.filter(task=task).exists())


class WatchApiTests(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Ship it")

    def tearDown(self):
        cache.clear()

    def _url(self, task=None):
        return (
            f"/api/v1/projects/{self.project.uuid}"
            f"/tasks/{(task or self.task).uuid}/watch"
        )

    def _row(self, user):
        return TaskWatcher.objects.filter(task=self.task, user=user).first()

    def test_put_sets_then_flips_the_state(self):
        self.client.force_authenticate(self.member)

        resp = self.client.put(self._url(), {"state": "watching"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, {"state": "watching"})
        self.assertFalse(self._row(self.member).muted)

        resp = self.client.put(self._url(), {"state": "muted"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self._row(self.member).muted)
        self.assertEqual(TaskWatcher.objects.filter(task=self.task).count(), 1)

    def test_put_rejects_an_unknown_state(self):
        self.client.force_authenticate(self.member)
        resp = self.client.put(self._url(), {"state": "shouting"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_delete_clears_and_is_idempotent(self):
        set_watch_state(self.task, self.member, muted=True)
        self.client.force_authenticate(self.member)

        resp = self.client.delete(self._url())
        self.assertEqual(resp.status_code, 204)
        self.assertIsNone(self._row(self.member))

        resp = self.client.delete(self._url())
        self.assertEqual(resp.status_code, 204)

    def test_outsider_gets_404(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.put(self._url(), {"state": "watching"}, format="json")
        self.assertEqual(resp.status_code, 404)

    def test_unknown_task_gets_404(self):
        other = create_task(self.project, self.admin, title="Elsewhere")
        other.delete()
        self.client.force_authenticate(self.member)
        resp = self.client.put(self._url(other), {"state": "watching"}, format="json")
        self.assertEqual(resp.status_code, 404)


class AutoWatchApiTests(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Ship it")

    def tearDown(self):
        cache.clear()

    def _comment(self, user, body="progress?"):
        self.client.force_authenticate(user)
        url = f"/api/v1/projects/{self.project.uuid}/tasks/{self.task.uuid}/comments"
        return self.client.post(url, {"body": body}, format="json")

    def test_commenting_auto_watches(self):
        resp = self._comment(self.member)
        self.assertEqual(resp.status_code, 201)
        self.assertFalse(
            TaskWatcher.objects.get(task=self.task, user=self.member).muted
        )

    def test_auto_watch_setting_off_leaves_no_row(self):
        set_setting(self.member, "projects", "auto_watch", False)
        self._comment(self.member)
        self.assertFalse(
            TaskWatcher.objects.filter(task=self.task, user=self.member).exists()
        )

    def test_commenting_never_unmutes(self):
        set_watch_state(self.task, self.member, muted=True)
        self._comment(self.member)
        self.assertTrue(TaskWatcher.objects.get(task=self.task, user=self.member).muted)

    def test_assigning_via_patch_auto_watches(self):
        self.client.force_authenticate(self.admin)
        url = f"/api/v1/projects/{self.project.uuid}/tasks/{self.task.uuid}"
        resp = self.client.patch(url, {"assignees": [self.member.pk]}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(
            TaskWatcher.objects.get(task=self.task, user=self.member).muted
        )


class PanelWatcherListTests(ProjectTestMixin, TestCase):
    def tearDown(self):
        cache.clear()

    def test_departed_watcher_is_not_listed_in_the_panel(self):
        from django.urls import reverse

        from workspace.projects.services.members import remove_member

        task = create_task(self.project, self.admin, title="Ship it")
        set_watch_state(task, self.admin, muted=False)
        set_watch_state(task, self.member, muted=False)
        remove_member(self.membership)

        self.client.force_login(self.admin)
        resp = self.client.get(
            reverse(
                "projects_ui:task_panel",
                kwargs={"project_uuid": self.project.uuid, "task_uuid": task.uuid},
            )
        )

        self.assertEqual(resp.status_code, 200)
        usernames = [w["username"] for w in resp.context["panel_watchers"]]
        self.assertIn("admin1", usernames)
        self.assertNotIn("member1", usernames)
