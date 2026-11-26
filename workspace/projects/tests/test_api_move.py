import uuid

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin


class BatchMoveApiTests(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.backlog = self.project.statuses.get(name="Backlog")
        self.todo = self.project.statuses.get(name="To do")
        self.t1 = create_task(self.project, self.admin, title="t1")
        self.t2 = create_task(self.project, self.admin, title="t2")
        self.t3 = create_task(self.project, self.admin, title="t3")
        self.url = f"/api/v1/projects/{self.project.uuid}/tasks/batch"

    def _move(self, tasks, status_uuid=None):
        return self.client.post(
            self.url,
            {
                "action": "move",
                "uuids": [str(getattr(task, "uuid", task)) for task in tasks],
                "data": {"status": str(status_uuid or self.todo.uuid)},
            },
            format="json",
        )

    def _titles(self, column):
        return [
            t.title
            for t in self.project.tasks.filter(status=column).order_by("position")
        ]

    def test_member_moves_selection_to_board(self):
        self.client.force_authenticate(self.member)
        response = self._move([self.t3, self.t1])
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._titles(self.todo), ["t1", "t3"])
        self.assertEqual(self._titles(self.backlog), ["t2"])

    def test_the_answer_is_the_tasks_named_as_stored(self):
        self.client.force_authenticate(self.member)
        response = self._move([self.t3, self.t1])
        self.assertEqual([t["title"] for t in response.data], ["t3", "t1"])
        self.assertEqual(
            {t["status"]["uuid"] for t in response.data}, {str(self.todo.uuid)}
        )

    def test_replaying_the_request_changes_nothing_more(self):
        self.client.force_authenticate(self.member)
        self._move([self.t1])
        again = self._move([self.t1])
        self.assertEqual(again.status_code, status.HTTP_200_OK)
        self.assertEqual(again.data, [])
        self.assertEqual(self._titles(self.todo), ["t1"])

    def test_one_unknown_task_refuses_the_whole_lot(self):
        absent = uuid.uuid4()
        self.client.force_authenticate(self.member)
        response = self._move([self.t1, absent])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(list(response.data), ["uuids"])
        self.assertIn(str(absent), response.data["uuids"][0])
        self.assertEqual(self._titles(self.todo), [])

    def test_a_task_of_another_project_answers_like_an_unknown_one(self):
        other = create_project(self.admin, name="Other")
        theirs = create_task(other, self.admin, title="theirs")
        absent = uuid.uuid4()
        self.client.force_authenticate(self.member)
        hidden = self._move([theirs])
        unknown = self._move([absent])
        self.assertEqual(hidden.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            hidden.content.replace(str(theirs.uuid).encode(), b"<uuid>"),
            unknown.content.replace(str(absent).encode(), b"<uuid>"),
        )

    def test_status_from_another_project_is_400(self):
        other = create_project(self.admin, name="Other")
        self.client.force_authenticate(self.member)
        response = self._move([self.t1], other.statuses.first().uuid)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_malformed_task_uuid_is_400(self):
        self.client.force_authenticate(self.member)
        response = self._move(["nope"])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_task_uuids_is_400(self):
        self.client.force_authenticate(self.member)
        response = self._move([self.t1, self.t1])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_oversized_task_list_is_400(self):
        self.client.force_authenticate(self.member)
        response = self._move([uuid.uuid4() for _ in range(1001)])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_an_unknown_action_is_400(self):
        self.client.force_authenticate(self.member)
        for body in (
            {"action": "explode", "uuids": [str(self.t1.uuid)]},
            {"uuids": [str(self.t1.uuid)]},
            ["not", "an", "object"],
        ):
            with self.subTest(body=body):
                response = self.client.post(self.url, body, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(list(response.data), ["action"])

    def test_archived_project_is_403(self):
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_authenticate(self.member)
        response = self._move([self.t1])
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_outsider_gets_404(self):
        self.client.force_authenticate(self.outsider)
        response = self._move([self.t1])
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_anonymous_is_rejected(self):
        response = self._move([self.t1])
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )
