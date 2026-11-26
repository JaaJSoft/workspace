import uuid

from rest_framework import status
from rest_framework.test import APITestCase

from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin


class AvailableActionsEndpointTests(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="t")
        self.url = "/api/v1/projects/available-actions"

    def _ask(self, *uuids):
        return self.client.post(
            self.url, {"uuids": [str(u) for u in uuids]}, format="json"
        )

    def test_mixed_uuids_return_actions_map(self):
        self.client.force_authenticate(self.admin)
        response = self._ask(self.project.uuid, self.task.uuid)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        project_ids = {a["id"] for a in response.data[str(self.project.uuid)]}
        task_ids = {a["id"] for a in response.data[str(self.task.uuid)]}
        self.assertIn("manage_members", project_ids)
        self.assertIn("edit", task_ids)

    def test_member_gets_member_view(self):
        self.client.force_authenticate(self.member)
        response = self._ask(self.project.uuid)
        self.assertEqual(response.data[str(self.project.uuid)], [])

    def test_a_row_out_of_reach_answers_like_one_that_does_not_exist(self):
        absent = uuid.uuid4()
        self.client.force_authenticate(self.outsider)
        hidden = self._ask(self.task.uuid)
        unknown = self._ask(absent)
        self.assertEqual(hidden.status_code, status.HTTP_200_OK)
        self.assertEqual(hidden.data, {str(self.task.uuid): []})
        self.assertEqual(unknown.data, {str(absent): []})

    def test_a_stale_uuid_does_not_cost_the_others_their_answer(self):
        self.client.force_authenticate(self.admin)
        response = self._ask(self.task.uuid, uuid.uuid4())
        self.assertTrue(response.data[str(self.task.uuid)])

    def test_empty_or_malformed_payload_is_400(self):
        self.client.force_authenticate(self.admin)
        for payload in ({}, {"uuids": []}, {"uuids": "x"}, {"uuids": ["nope"]}, []):
            with self.subTest(payload=payload):
                response = self.client.post(self.url, payload, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_too_many_uuids_is_400(self):
        self.client.force_authenticate(self.admin)
        response = self._ask(*(uuid.uuid4() for _ in range(1001)))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
