"""Bulk edit and bulk delete endpoints behind the selection toolbars."""

import datetime
import uuid as uuid_module

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.notifications.models import Notification
from workspace.projects.models import TaskEvent
from workspace.projects.services.members import add_member
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin

User = get_user_model()


class BulkTestsBase(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.t1 = create_task(self.project, self.admin, title="t1")
        self.t2 = create_task(self.project, self.admin, title="t2")
        self.t3 = create_task(self.project, self.admin, title="t3")
        self.bug = self.project.labels.create(name="Bug", color="#ff0000")
        self.url = f"/api/v1/projects/{self.project.uuid}/tasks/bulk"
        self.delete_url = f"/api/v1/projects/{self.project.uuid}/tasks/bulk-delete"

    def tearDown(self):
        cache.clear()

    def _post(self, payload, user=None, url=None):
        self.client.force_authenticate(user or self.member)
        return self.client.post(url or self.url, payload, format="json")

    def _uuids(self, *tasks):
        return [str(t.uuid) for t in tasks]

    def _events(self, task, type):
        return TaskEvent.objects.filter(task=task, type=type)


class BulkEditTests(BulkTestsBase):
    def test_assign_adds_the_user_and_records_one_assigned_event_per_task(self):
        self.t1.assignees.add(self.admin)

        response = self._post(
            {"tasks": self._uuids(self.t1, self.t2), "assign": [self.member.pk]}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"success": True, "updated": 2, "skipped": 0})
        # Add semantics: the existing assignee stays.
        self.assertEqual(
            set(self.t1.assignees.values_list("pk", flat=True)),
            {self.admin.pk, self.member.pk},
        )
        self.assertEqual(list(self.t2.assignees.all()), [self.member])
        self.assertFalse(self.t3.assignees.exists())
        for task in (self.t1, self.t2):
            self.assertEqual(self._events(task, TaskEvent.Type.ASSIGNED).count(), 1)
            self.assertFalse(self._events(task, TaskEvent.Type.UPDATED).exists())
        self.assertFalse(self._events(self.t3, TaskEvent.Type.ASSIGNED).exists())

    def test_assigning_an_existing_assignee_is_a_no_op(self):
        self.t1.assignees.add(self.member)

        response = self._post(
            {"tasks": self._uuids(self.t1), "assign": [self.member.pk]}
        )

        self.assertEqual(response.data["updated"], 0)
        self.assertFalse(self._events(self.t1, TaskEvent.Type.ASSIGNED).exists())

    def test_bulk_assign_collapses_to_one_notification_per_user(self):
        response = self._post(
            {
                "tasks": self._uuids(self.t1, self.t2, self.t3),
                "assign": [self.admin.pk],
            }
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        notifs = Notification.objects.filter(recipient=self.admin)
        self.assertEqual(notifs.count(), 1)
        notif = notifs.get()
        self.assertEqual(notif.actor, self.member)
        self.assertIn("3 tasks", notif.title)
        self.assertIn(self.project.name, notif.title)
        self.assertEqual(
            notif.url, f"/projects/{self.project.uuid}/tasks?assignee={self.admin.pk}"
        )
        # The actor is never notified of their own assignment.
        self.assertFalse(Notification.objects.filter(recipient=self.member).exists())

    def test_single_task_assignment_keeps_the_task_notification(self):
        self._post({"tasks": self._uuids(self.t1), "assign": [self.admin.pk]})

        notif = Notification.objects.get(recipient=self.admin)
        self.assertEqual(notif.task, self.t1)
        self.assertEqual(notif.stream, "assignment")

    def test_unassign_removes_the_user_and_records_an_update(self):
        self.t1.assignees.add(self.member, self.admin)
        self.t2.assignees.add(self.admin)

        response = self._post(
            {"tasks": self._uuids(self.t1, self.t2), "unassign": [self.member.pk]}
        )

        self.assertEqual(response.data["updated"], 1)
        self.assertEqual(list(self.t1.assignees.all()), [self.admin])
        self.assertEqual(list(self.t2.assignees.all()), [self.admin])
        self.assertTrue(self._events(self.t1, TaskEvent.Type.UPDATED).exists())
        self.assertFalse(self._events(self.t2, TaskEvent.Type.UPDATED).exists())

    def test_labels_are_added_and_removed_as_sets(self):
        feature = self.project.labels.create(name="Feature", color="#00ff00")
        self.t1.labels.add(self.bug)
        self.t2.labels.add(feature)

        response = self._post(
            {
                "tasks": self._uuids(self.t1, self.t2),
                "add_labels": [str(feature.uuid)],
                "remove_labels": [str(self.bug.uuid)],
            }
        )

        self.assertEqual(response.data["updated"], 1)
        self.assertEqual(list(self.t1.labels.all()), [feature])
        self.assertEqual(list(self.t2.labels.all()), [feature])
        self.assertTrue(self._events(self.t1, TaskEvent.Type.UPDATED).exists())
        self.assertFalse(self._events(self.t2, TaskEvent.Type.UPDATED).exists())

    def test_priority_and_due_date_are_set_where_they_differ(self):
        self.t1.priority = "high"
        self.t1.save(update_fields=["priority"])
        before = self.t1.updated_at

        response = self._post(
            {
                "tasks": self._uuids(self.t1, self.t2),
                "priority": "high",
                "due_date": "2030-01-15",
            }
        )

        self.assertEqual(response.data["updated"], 2)
        for task in (self.t1, self.t2):
            task.refresh_from_db()
            self.assertEqual(task.priority, "high")
            self.assertEqual(task.due_date, datetime.date(2030, 1, 15))
            self.assertEqual(self._events(task, TaskEvent.Type.UPDATED).count(), 1)
        self.assertGreater(self.t1.updated_at, before)

    def test_null_due_date_clears_it(self):
        self.t1.due_date = datetime.date(2030, 1, 1)
        self.t1.save(update_fields=["due_date"])

        response = self._post(
            {"tasks": self._uuids(self.t1, self.t2), "due_date": None}
        )

        self.assertEqual(response.data["updated"], 1)
        self.t1.refresh_from_db()
        self.assertIsNone(self.t1.due_date)

    def test_due_date_before_a_planned_start_skips_that_task_only(self):
        self.t1.start_date = datetime.date(2030, 6, 1)
        self.t1.save(update_fields=["start_date"])

        response = self._post(
            {
                "tasks": self._uuids(self.t1, self.t2),
                "due_date": "2030-01-15",
                "priority": "urgent",
            }
        )

        self.assertEqual(response.data, {"success": True, "updated": 1, "skipped": 1})
        self.t1.refresh_from_db()
        self.t2.refresh_from_db()
        self.assertIsNone(self.t1.due_date)
        # The refused task is left whole, not half-edited.
        self.assertEqual(self.t1.priority, "medium")
        self.assertEqual(self.t2.due_date, datetime.date(2030, 1, 15))
        self.assertEqual(self.t2.priority, "urgent")
        self.assertFalse(self._events(self.t1, TaskEvent.Type.UPDATED).exists())

    def test_tasks_of_another_project_are_ignored(self):
        other = create_project(self.admin, name="Other")
        foreign = create_task(other, self.admin, title="foreign")

        response = self._post(
            {"tasks": self._uuids(self.t1, foreign), "priority": "low"}
        )

        self.assertEqual(response.data["updated"], 1)
        foreign.refresh_from_db()
        self.assertEqual(foreign.priority, "medium")

    def test_no_operation_is_400(self):
        response = self._post({"tasks": self._uuids(self.t1)})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_conflicting_operations_are_400(self):
        response = self._post(
            {
                "tasks": self._uuids(self.t1),
                "assign": [self.member.pk],
                "unassign": [self.member.pk],
            }
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response = self._post(
            {
                "tasks": self._uuids(self.t1),
                "add_labels": [str(self.bug.uuid)],
                "remove_labels": [str(self.bug.uuid)],
            }
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_member_assignee_is_400(self):
        response = self._post(
            {"tasks": self._uuids(self.t1), "assign": [self.outsider.pk]}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(self.t1.assignees.exists())

    def test_label_of_another_project_is_400(self):
        other = create_project(self.admin, name="Other")
        foreign_label = other.labels.create(name="Nope", color="#000000")
        response = self._post(
            {"tasks": self._uuids(self.t1), "add_labels": [str(foreign_label.uuid)]}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_priority_is_400(self):
        response = self._post({"tasks": self._uuids(self.t1), "priority": "meh"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_malformed_and_oversized_task_lists_are_400(self):
        response = self._post({"tasks": ["nope"], "priority": "low"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response = self._post(
            {
                "tasks": [str(uuid_module.uuid4()) for _ in range(1001)],
                "priority": "low",
            }
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_archived_project_is_403(self):
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        response = self._post({"tasks": self._uuids(self.t1), "priority": "low"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_outsider_gets_404(self):
        response = self._post(
            {"tasks": self._uuids(self.t1), "priority": "low"}, user=self.outsider
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_anonymous_is_rejected(self):
        response = self.client.post(
            self.url, {"tasks": self._uuids(self.t1), "priority": "low"}, format="json"
        )
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )


class BulkDeleteTests(BulkTestsBase):
    def test_deletes_the_selection_with_one_event_each(self):
        response = self._post(
            {"tasks": self._uuids(self.t1, self.t3)}, url=self.delete_url
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"success": True, "deleted": 2})
        self.assertEqual(
            list(self.project.tasks.values_list("title", flat=True)), ["t2"]
        )
        deleted = TaskEvent.objects.filter(
            project=self.project, type=TaskEvent.Type.DELETED
        )
        self.assertEqual(
            sorted(deleted.values_list("task_title", flat=True)), ["t1", "t3"]
        )
        self.assertTrue(all(ev.actor == self.member for ev in deleted))

    def test_unknown_and_foreign_tasks_are_skipped(self):
        other = create_project(self.admin, name="Other")
        foreign = create_task(other, self.admin, title="foreign")

        response = self._post(
            {"tasks": self._uuids(self.t1, foreign) + [str(uuid_module.uuid4())]},
            url=self.delete_url,
        )

        self.assertEqual(response.data["deleted"], 1)
        self.assertTrue(other.tasks.filter(pk=foreign.pk).exists())

    def test_oversized_list_is_400(self):
        response = self._post(
            {"tasks": [str(uuid_module.uuid4()) for _ in range(1001)]},
            url=self.delete_url,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_archived_project_is_403(self):
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        response = self._post({"tasks": self._uuids(self.t1)}, url=self.delete_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(self.project.tasks.filter(pk=self.t1.pk).exists())

    def test_outsider_gets_404(self):
        response = self._post(
            {"tasks": self._uuids(self.t1)}, user=self.outsider, url=self.delete_url
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(self.project.tasks.filter(pk=self.t1.pk).exists())


class BulkActionFlagTests(BulkTestsBase):
    def test_selection_actions_are_flagged_bulk(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            "/api/v1/projects/actions",
            {"uuids": self._uuids(self.t1)},
            format="json",
        )
        by_id = {a["id"]: a for a in response.data[str(self.t1.uuid)]}
        for action_id in (
            "move",
            "assign",
            "set_due",
            "set_labels",
            "set_priority",
            "delete",
        ):
            self.assertTrue(by_id[action_id]["bulk"], action_id)
        for action_id in ("edit", "comment", "attach", "link"):
            self.assertFalse(by_id[action_id]["bulk"], action_id)

    def test_added_member_can_be_bulk_assigned(self):
        extra = User.objects.create_user(
            username="extra1", email="extra1@test.com", password="pass123"
        )
        add_member(self.project, extra)
        response = self._post({"tasks": self._uuids(self.t1), "assign": [extra.pk]})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(self.t1.assignees.all()), [extra])
