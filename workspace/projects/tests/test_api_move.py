from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin


class MoveApiTests(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.backlog = self.project.statuses.get(name="Backlog")
        self.todo = self.project.statuses.get(name="To do")
        self.t1 = create_task(self.project, self.admin, title="t1")
        self.t2 = create_task(self.project, self.admin, title="t2")
        self.t3 = create_task(self.project, self.admin, title="t3")
        self.url = f"/api/v1/projects/{self.project.uuid}/tasks/move"

    def test_member_moves_selection_to_board(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.url,
            {
                "status": str(self.todo.uuid),
                "tasks": [str(self.t3.uuid), str(self.t1.uuid)],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["moved"], 2)
        titles = [
            t.title
            for t in self.project.tasks.filter(status=self.todo).order_by("position")
        ]
        self.assertEqual(titles, ["t1", "t3"])
        backlog_titles = [
            t.title
            for t in self.project.tasks.filter(status=self.backlog).order_by("position")
        ]
        self.assertEqual(backlog_titles, ["t2"])

    def test_status_from_another_project_is_400(self):
        other = create_project(self.admin, name="Other")
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.url,
            {
                "status": str(other.statuses.first().uuid),
                "tasks": [str(self.t1.uuid)],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_malformed_task_uuid_is_400(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.url,
            {"status": str(self.todo.uuid), "tasks": ["nope"]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_task_uuids_is_400(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.url,
            {
                "status": str(self.todo.uuid),
                "tasks": [str(self.t1.uuid), str(self.t1.uuid)],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_archived_project_is_403(self):
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.url,
            {"status": str(self.todo.uuid), "tasks": [str(self.t1.uuid)]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_outsider_gets_404(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.post(
            self.url,
            {"status": str(self.todo.uuid), "tasks": [str(self.t1.uuid)]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_anonymous_is_rejected(self):
        response = self.client.post(
            self.url,
            {"status": str(self.todo.uuid), "tasks": [str(self.t1.uuid)]},
            format="json",
        )
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )
