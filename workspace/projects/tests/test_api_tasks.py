from rest_framework import status
from rest_framework.test import APITestCase

from workspace.projects.models import Task
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin


class TaskApiMixin(ProjectTestMixin):
    def setUp(self):
        super().setUp()
        self.backlog = self.project.statuses.get(name="Backlog")
        self.todo = self.project.statuses.get(name="To do")
        self.done = self.project.statuses.get(name="Done")
        self.tasks_url = f"/api/v1/projects/{self.project.uuid}/tasks"


class TaskListCreateTests(TaskApiMixin, APITestCase):
    def test_member_creates_task_defaulting_to_backlog(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(self.tasks_url, {"title": "Ship it"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], self.backlog.uuid)
        self.assertEqual(response.data["status_category"], "backlog")

    def test_create_with_assignee_and_label(self):
        label = self.project.labels.create(name="bug", color="#ff0000")
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.tasks_url,
            {
                "title": "Fix",
                "status": str(self.todo.uuid),
                "assignees": [str(self.admin.pk)],
                "labels": [str(label.uuid)],
                "priority": "high",
                "due_date": "2026-08-01",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        task = Task.objects.get(uuid=response.data["uuid"])
        self.assertEqual(list(task.assignees.all()), [self.admin])
        self.assertEqual(task.status, self.todo)

    def test_assignee_must_be_project_member(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.tasks_url,
            {"title": "Fix", "assignees": [str(self.outsider.pk)]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_status_from_another_project_rejected(self):
        from workspace.projects.services.projects import create_project

        other = create_project(self.admin, name="Other")
        foreign_status = other.statuses.first()
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.tasks_url,
            {"title": "Fix", "status": str(foreign_status.uuid)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_filters(self):
        t1 = create_task(self.project, self.admin, title="alpha")
        create_task(self.project, self.admin, title="beta", status=self.todo)
        t1.assignees.add(self.member)
        self.client.force_authenticate(self.member)

        response = self.client.get(self.tasks_url, {"status": str(self.todo.uuid)})
        self.assertEqual([t["title"] for t in response.data], ["beta"])

        response = self.client.get(self.tasks_url, {"assignee": str(self.member.pk)})
        self.assertEqual([t["title"] for t in response.data], ["alpha"])

        response = self.client.get(self.tasks_url, {"q": "alpha"})
        self.assertEqual([t["title"] for t in response.data], ["alpha"])

    def test_task_search_matches_description(self):
        # `?q=` used to be a title-only icontains; full-text search must
        # also match words that appear only in the description.
        task = create_task(self.project, self.admin, title="Quarterly review")
        task.description = "prepare the pelican slides"
        task.save(update_fields=["description"])
        self.client.force_authenticate(self.member)
        response = self.client.get(self.tasks_url, {"q": "pelican"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(str(task.uuid), [t["uuid"] for t in response.data])

    def test_malformed_filter_uuid_is_400(self):
        self.client.force_authenticate(self.member)
        response = self.client.get(self.tasks_url, {"status": "not-a-uuid"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_archived_project_rejects_creation(self):
        from django.utils import timezone

        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_authenticate(self.member)
        response = self.client.post(self.tasks_url, {"title": "Nope"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class TaskDetailTests(TaskApiMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="t")
        self.detail_url = f"{self.tasks_url}/{self.task.uuid}"

    def test_member_updates_fields(self):
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            self.detail_url, {"title": "renamed", "priority": "urgent"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, "renamed")

    def test_patching_status_to_done_sets_completed_at(self):
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            self.detail_url, {"status": str(self.done.uuid)}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.task.refresh_from_db()
        self.assertIsNotNone(self.task.completed_at)

    def test_member_deletes_task(self):
        self.client.force_authenticate(self.member)
        response = self.client.delete(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Task.objects.filter(uuid=self.task.uuid).exists())

    def test_outsider_gets_404(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
