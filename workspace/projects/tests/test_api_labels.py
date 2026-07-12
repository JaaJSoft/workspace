from rest_framework import status
from rest_framework.test import APITestCase

from .base import ProjectTestMixin


class LabelApiTests(ProjectTestMixin, APITestCase):
    def test_member_lists_labels(self):
        self.project.labels.create(name="bug", color="#ff0000")
        self.client.force_authenticate(self.member)
        response = self.client.get(f"/api/v1/projects/{self.project.uuid}/labels")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]["name"], "bug")

    def test_admin_creates_label(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            f"/api/v1/projects/{self.project.uuid}/labels",
            {"name": "bug", "color": "#ff0000"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.project.labels.count(), 1)

    def test_member_cannot_create_label(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            f"/api/v1/projects/{self.project.uuid}/labels",
            {"name": "bug"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_duplicate_name_is_400(self):
        self.project.labels.create(name="bug", color="#ff0000")
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            f"/api/v1/projects/{self.project.uuid}/labels",
            {"name": "bug"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_updates_and_deletes_label(self):
        label = self.project.labels.create(name="bug", color="#ff0000")
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/v1/projects/{self.project.uuid}/labels/{label.uuid}",
            {"name": "defect"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response = self.client.delete(
            f"/api/v1/projects/{self.project.uuid}/labels/{label.uuid}"
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.project.labels.count(), 0)


class StatusApiTests(ProjectTestMixin, APITestCase):
    def test_member_lists_statuses_in_order(self):
        self.client.force_authenticate(self.member)
        response = self.client.get(f"/api/v1/projects/{self.project.uuid}/statuses")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [s["name"] for s in response.data],
            ["Backlog", "To do", "In progress", "Done"],
        )
        self.assertEqual(response.data[0]["category"], "backlog")

    def test_outsider_gets_404(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.get(f"/api/v1/projects/{self.project.uuid}/statuses")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
