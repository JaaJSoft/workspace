"""Tests for assignment notifications and the ASSIGNED activity event."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APITestCase

from workspace.notifications.models import Notification
from workspace.projects.models import TaskEvent
from workspace.projects.services.members import add_member
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin

User = get_user_model()


class AssignmentTestsBase(ProjectTestMixin, APITestCase):
    def tearDown(self):
        cache.clear()

    def _task(self, assignees=()):
        return create_task(
            self.project, self.admin, title="Ship it", assignees=list(assignees)
        )

    def _patch_task(self, task, payload):
        self.client.force_authenticate(self.admin)
        url = f"/api/v1/projects/{self.project.uuid}/tasks/{task.uuid}"
        return self.client.patch(url, payload, format="json")

    def _unread(self, task, user):
        return Notification.objects.filter(
            task=task, recipient=user, read_at__isnull=True
        )

    def _events(self, task, type):
        return TaskEvent.objects.filter(task=task, type=type)


class AssignmentNotificationTests(AssignmentTestsBase):
    def test_assigning_notifies_the_new_assignee(self):
        task = self._task()

        resp = self._patch_task(task, {"assignees": [self.member.pk]})

        self.assertEqual(resp.status_code, 200)
        notif = self._unread(task, self.member).get()
        self.assertEqual(notif.origin, "projects")
        self.assertEqual(notif.stream, "assignment")
        self.assertEqual(notif.actor, self.admin)
        self.assertIn("assigned you", notif.title)
        self.assertIn("Ship it", notif.title)
        self.assertEqual(
            notif.url, f"/projects/{self.project.uuid}/board?task={task.uuid}"
        )

    def test_assignment_records_its_own_event_not_an_update(self):
        task = self._task()

        self._patch_task(task, {"assignees": [self.member.pk]})

        event = self._events(task, TaskEvent.Type.ASSIGNED).get()
        self.assertEqual(event.actor, self.admin)
        self.assertFalse(self._events(task, TaskEvent.Type.UPDATED).exists())

    def test_self_assignment_is_silent_but_recorded(self):
        task = self._task()

        self._patch_task(task, {"assignees": [self.admin.pk]})

        self.assertEqual(Notification.objects.count(), 0)
        self.assertTrue(self._events(task, TaskEvent.Type.ASSIGNED).exists())

    def test_existing_assignees_are_not_renotified(self):
        extra = User.objects.create_user(
            username="extra1", email="extra1@test.com", password="pass123"
        )
        add_member(self.project, extra)
        task = self._task(assignees=[self.member])
        Notification.objects.all().delete()

        self._patch_task(task, {"assignees": [self.member.pk, extra.pk]})

        self.assertTrue(self._unread(task, extra).exists())
        self.assertFalse(self._unread(task, self.member).exists())

    def test_unassigning_records_a_plain_update(self):
        task = self._task(assignees=[self.member])
        Notification.objects.all().delete()

        self._patch_task(task, {"assignees": []})

        self.assertEqual(Notification.objects.count(), 0)
        self.assertFalse(self._events(task, TaskEvent.Type.ASSIGNED).exists())
        self.assertTrue(self._events(task, TaskEvent.Type.UPDATED).exists())

    def test_creating_with_assignees_notifies_them_except_the_creator(self):
        task = self._task(assignees=[self.member, self.admin])

        self.assertTrue(self._unread(task, self.member).exists())
        self.assertFalse(self._unread(task, self.admin).exists())
        # Creation already lands in the feed as CREATED.
        self.assertFalse(self._events(task, TaskEvent.Type.ASSIGNED).exists())
