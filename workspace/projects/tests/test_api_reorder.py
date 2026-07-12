from rest_framework import status
from rest_framework.test import APITestCase

from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin


class ReorderApiTests(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.backlog = self.project.statuses.get(name="Backlog")
        self.done = self.project.statuses.get(name="Done")
        self.t1 = create_task(self.project, self.admin, title="t1")
        self.t2 = create_task(self.project, self.admin, title="t2")
        self.url = f"/api/v1/projects/{self.project.uuid}/tasks/reorder"

    def test_member_reorders_backlog(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.url,
            {
                "status": str(self.backlog.uuid),
                "order": [str(self.t2.uuid), str(self.t1.uuid)],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        titles = [
            t.title
            for t in self.project.tasks.filter(status=self.backlog).order_by("position")
        ]
        self.assertEqual(titles, ["t2", "t1"])

    def test_cross_column_drop_moves_task(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.url,
            {"status": str(self.done.uuid), "order": [str(self.t1.uuid)]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.status, self.done)
        self.assertIsNotNone(self.t1.completed_at)

    def test_status_from_another_project_is_400(self):
        from workspace.projects.services.projects import create_project

        other = create_project(self.admin, name="Other")
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.url,
            {
                "status": str(other.statuses.first().uuid),
                "order": [str(self.t1.uuid)],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_malformed_order_item_is_400(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.url,
            {"status": str(self.backlog.uuid), "order": ["nope"]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_archived_project_is_403(self):
        from django.utils import timezone

        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.url,
            {"status": str(self.backlog.uuid), "order": [str(self.t1.uuid)]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_duplicate_uuid_in_order_is_400(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.url,
            {
                "status": str(self.backlog.uuid),
                "order": [str(self.t1.uuid), str(self.t1.uuid)],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_string_order_item_is_400(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.url,
            {"status": str(self.backlog.uuid), "order": [42]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
