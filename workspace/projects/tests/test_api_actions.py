from rest_framework import status
from rest_framework.test import APITestCase

from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin


class ActionsEndpointTests(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="t")
        self.url = "/api/v1/projects/actions"

    def test_mixed_uuids_return_actions_map(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.url,
            {"uuids": [str(self.project.uuid), str(self.task.uuid)]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        project_ids = {a["id"] for a in response.data[str(self.project.uuid)]}
        task_ids = {a["id"] for a in response.data[str(self.task.uuid)]}
        self.assertIn("manage_members", project_ids)
        self.assertIn("edit", task_ids)

    def test_member_gets_member_view(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.url, {"uuids": [str(self.project.uuid)]}, format="json"
        )
        self.assertEqual(response.data[str(self.project.uuid)], [])

    def test_invisible_uuid_is_404(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.post(
            self.url, {"uuids": [str(self.task.uuid)]}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_empty_or_malformed_payload_is_400(self):
        self.client.force_authenticate(self.admin)
        for payload in ({}, {"uuids": []}, {"uuids": "x"}, {"uuids": ["nope"]}):
            response = self.client.post(self.url, payload, format="json")
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_too_many_uuids_is_400(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.url, {"uuids": [str(self.task.uuid)] * 201}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
