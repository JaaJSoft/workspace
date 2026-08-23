"""Tests for per-user notification levels (module-wide and per-project)."""

from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APITestCase

from workspace.notifications.models import Notification
from workspace.projects.models import ProjectNotificationLevel, TaskReminder
from workspace.projects.services.comments import notify_comment_added
from workspace.projects.services.members import remove_member
from workspace.projects.services.notification_levels import (
    apply_levels,
    module_level,
    user_levels,
)
from workspace.projects.services.tasks import create_task
from workspace.users.services.settings import set_setting

from .base import ProjectTestMixin

Level = ProjectNotificationLevel.Level


def _override(project, user, level):
    return ProjectNotificationLevel.objects.create(
        project=project, user=user, level=level
    )


class ResolutionTests(ProjectTestMixin, TestCase):
    def tearDown(self):
        cache.clear()

    def test_defaults_to_all(self):
        self.assertEqual(module_level(self.member), Level.ALL)
        levels = user_levels(self.project.uuid, [self.member])
        self.assertEqual(levels, {self.member.pk: Level.ALL})

    def test_module_setting_is_honored(self):
        set_setting(self.member, "projects", "notify_level", "in_app")
        self.assertEqual(module_level(self.member), Level.IN_APP)
        levels = user_levels(self.project.uuid, [self.member])
        self.assertEqual(levels[self.member.pk], Level.IN_APP)

    def test_garbage_module_setting_falls_back_to_all(self):
        set_setting(self.member, "projects", "notify_level", "push-only-please")
        self.assertEqual(module_level(self.member), Level.ALL)

    def test_project_override_wins_over_module_setting(self):
        set_setting(self.member, "projects", "notify_level", "none")
        _override(self.project, self.member, Level.ALL)
        levels = user_levels(self.project.uuid, [self.member])
        self.assertEqual(levels[self.member.pk], Level.ALL)

    def test_apply_levels_drops_muted_and_caps_in_app(self):
        _override(self.project, self.member, Level.NONE)
        set_setting(self.admin, "projects", "notify_level", "in_app")

        recipients, priority_map = apply_levels(
            self.project.uuid, [self.admin, self.member]
        )

        self.assertEqual(recipients, [self.admin])
        self.assertEqual(priority_map, {self.admin.pk: "low"})

    def test_apply_levels_caps_an_elevated_priority_map_entry(self):
        _override(self.project, self.member, Level.IN_APP)

        recipients, priority_map = apply_levels(
            self.project.uuid, [self.member], priority_map={self.member.pk: "high"}
        )

        self.assertEqual(recipients, [self.member])
        self.assertEqual(priority_map[self.member.pk], "low")


class FanOutTests(ProjectTestMixin, TestCase):
    def tearDown(self):
        cache.clear()

    def _notifications(self, user):
        return Notification.objects.filter(recipient=user)

    def test_assignment_skips_a_muted_recipient(self):
        _override(self.project, self.member, Level.NONE)

        create_task(self.project, self.admin, title="Ship it", assignees=[self.member])

        self.assertFalse(self._notifications(self.member).exists())

    def test_assignment_is_low_priority_for_an_in_app_recipient(self):
        _override(self.project, self.member, Level.IN_APP)

        create_task(self.project, self.admin, title="Ship it", assignees=[self.member])

        notif = self._notifications(self.member).get()
        self.assertEqual(notif.priority, "low")

    def test_comment_skips_a_muted_recipient(self):
        task = create_task(
            self.project, self.admin, title="Ship it", assignees=[self.member]
        )
        Notification.objects.all().delete()
        _override(self.project, self.member, Level.NONE)

        notify_comment_added(task, self.admin, "progress?")

        self.assertFalse(self._notifications(self.member).exists())

    def test_mention_is_capped_at_low_for_an_in_app_recipient(self):
        task = create_task(self.project, self.admin, title="Ship it")
        _override(self.project, self.member, Level.IN_APP)

        notify_comment_added(task, self.admin, "ping @member1")

        notif = self._notifications(self.member).get()
        self.assertIn("mentioned you", notif.title)
        self.assertEqual(notif.priority, "low")

    def test_module_setting_gates_fan_out_without_an_override(self):
        set_setting(self.member, "projects", "notify_level", "none")

        create_task(self.project, self.admin, title="Ship it", assignees=[self.member])

        self.assertFalse(self._notifications(self.member).exists())


class ReminderCronTests(ProjectTestMixin, TestCase):
    def tearDown(self):
        cache.clear()

    def _due_task(self):
        from django.utils import timezone

        task = create_task(
            self.project,
            self.admin,
            title="Ship it",
            due_date=timezone.localdate(),
            assignees=[self.member],
        )
        Notification.objects.all().delete()
        return task

    def _run_cron(self):
        from unittest import mock

        from django.utils import timezone

        from workspace.projects.tasks import notify_due_tasks

        frozen = timezone.now().replace(hour=12, minute=15, second=0, microsecond=0)
        with mock.patch("django.utils.timezone.now", return_value=frozen):
            return notify_due_tasks()

    def test_muted_assignee_gets_no_reminder_and_none_is_claimed(self):
        task = self._due_task()
        _override(self.project, self.member, Level.NONE)

        sent = self._run_cron()

        self.assertEqual(sent, 0)
        self.assertFalse(Notification.objects.filter(task=task).exists())
        self.assertFalse(TaskReminder.objects.filter(task=task).exists())

    def test_unmuting_rearms_the_skipped_reminder(self):
        task = self._due_task()
        override = _override(self.project, self.member, Level.NONE)
        self._run_cron()

        override.delete()
        sent = self._run_cron()

        self.assertEqual(sent, 1)
        self.assertTrue(
            Notification.objects.filter(task=task, recipient=self.member).exists()
        )

    def test_in_app_assignee_gets_a_low_priority_reminder(self):
        task = self._due_task()
        _override(self.project, self.member, Level.IN_APP)

        sent = self._run_cron()

        self.assertEqual(sent, 1)
        notif = Notification.objects.filter(task=task, recipient=self.member).get()
        self.assertEqual(notif.priority, "low")
        self.assertEqual(notif.stream, "reminder")


class NotificationLevelApiTests(ProjectTestMixin, APITestCase):
    def tearDown(self):
        cache.clear()

    def _url(self, project=None):
        return f"/api/v1/projects/{(project or self.project).uuid}/notification-level"

    def _row(self, user):
        return ProjectNotificationLevel.objects.filter(
            project=self.project, user=user
        ).first()

    def test_put_creates_then_updates_the_override(self):
        self.client.force_authenticate(self.member)

        resp = self.client.put(self._url(), {"level": "in_app"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, {"level": "in_app"})
        self.assertEqual(self._row(self.member).level, Level.IN_APP)

        resp = self.client.put(self._url(), {"level": "none"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._row(self.member).level, Level.NONE)
        self.assertEqual(
            ProjectNotificationLevel.objects.filter(user=self.member).count(), 1
        )

    def test_put_rejects_an_unknown_level(self):
        self.client.force_authenticate(self.member)
        resp = self.client.put(self._url(), {"level": "loud"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_delete_removes_the_override_and_is_idempotent(self):
        _override(self.project, self.member, Level.NONE)
        self.client.force_authenticate(self.member)

        resp = self.client.delete(self._url())
        self.assertEqual(resp.status_code, 204)
        self.assertIsNone(self._row(self.member))

        resp = self.client.delete(self._url())
        self.assertEqual(resp.status_code, 204)

    def test_outsider_gets_404(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.put(self._url(), {"level": "none"}, format="json")
        self.assertEqual(resp.status_code, 404)

    def test_group_granted_user_can_override(self):
        group = Group.objects.create(name="devs")
        self.outsider.groups.add(group)
        self.project.groups.add(group)
        self.client.force_authenticate(self.outsider)

        resp = self.client.put(self._url(), {"level": "in_app"}, format="json")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._row(self.outsider).level, Level.IN_APP)


class CleanupOnDepartureTests(ProjectTestMixin, TestCase):
    def tearDown(self):
        cache.clear()

    def test_removing_a_member_drops_their_override(self):
        _override(self.project, self.member, Level.NONE)

        remove_member(self.membership)

        self.assertFalse(
            ProjectNotificationLevel.objects.filter(user=self.member).exists()
        )

    def test_override_survives_when_a_group_grant_remains(self):
        group = Group.objects.create(name="devs")
        self.member.groups.add(group)
        self.project.groups.add(group)
        _override(self.project, self.member, Level.NONE)

        remove_member(self.membership)

        self.assertTrue(
            ProjectNotificationLevel.objects.filter(user=self.member).exists()
        )
