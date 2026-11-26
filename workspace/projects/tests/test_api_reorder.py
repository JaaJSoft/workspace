import uuid

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.projects.services.projects import create_project
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

    def _reorder(self, column, uuids):
        return self.client.post(
            self.url,
            {"status": str(column.uuid), "uuids": [str(u) for u in uuids]},
            format="json",
        )

    def _titles(self, column):
        return [
            t.title
            for t in self.project.tasks.filter(status=column).order_by("position")
        ]

    def test_member_reorders_backlog(self):
        self.client.force_authenticate(self.member)
        response = self._reorder(self.backlog, [self.t2.uuid, self.t1.uuid])
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self._titles(self.backlog), ["t2", "t1"])

    def test_cross_column_drop_moves_task(self):
        self.client.force_authenticate(self.member)
        response = self._reorder(self.done, [self.t1.uuid])
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.status, self.done)
        self.assertIsNotNone(self.t1.completed_at)

    def test_tasks_left_out_keep_their_order_after_the_listed_ones(self):
        t3 = create_task(self.project, self.admin, title="t3")
        self.client.force_authenticate(self.member)
        self._reorder(self.backlog, [t3.uuid])
        self.assertEqual(self._titles(self.backlog), ["t3", "t1", "t2"])

    def test_one_unknown_task_refuses_the_whole_order(self):
        absent = uuid.uuid4()
        self.client.force_authenticate(self.member)
        response = self._reorder(self.backlog, [self.t2.uuid, absent, self.t1.uuid])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(str(absent), response.data["uuids"][0])
        self.assertEqual(self._titles(self.backlog), ["t1", "t2"])

    def test_status_from_another_project_is_400(self):
        other = create_project(self.admin, name="Other")
        self.client.force_authenticate(self.member)
        response = self._reorder(other.statuses.first(), [self.t1.uuid])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(list(response.data), ["status"])

    def test_malformed_or_repeated_uuids_are_400(self):
        self.client.force_authenticate(self.member)
        for uuids in (["nope"], [42], [self.t1.uuid, self.t1.uuid], []):
            with self.subTest(uuids=uuids):
                response = self._reorder(self.backlog, uuids)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(list(response.data), ["uuids"])

    def test_archived_project_is_403(self):
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_authenticate(self.member)
        response = self._reorder(self.backlog, [self.t1.uuid])
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
