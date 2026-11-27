import uuid as uuid_module

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.projects.services.projects import create_project
from workspace.projects.services.subtasks import create_subtask
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin


class SubtaskApiTests(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Ship the thing")
        self.base_url = f"/api/v1/projects/tasks/{self.task.uuid}/subtasks"

    def _detail_url(self, subtask):
        return f"/api/v1/projects/subtasks/{subtask.uuid}"

    # ── List ──────────────────────────────────────────────

    def test_member_lists_subtasks_in_position_order(self):
        create_subtask(self.task, "first")
        create_subtask(self.task, "second")
        self.client.force_authenticate(self.member)
        response = self.client.get(self.base_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([s["title"] for s in response.data], ["first", "second"])
        self.assertEqual(
            set(response.data[0].keys()),
            {"uuid", "title", "is_done", "position", "created_at"},
        )

    def test_outsider_gets_404(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.get(self.base_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_task_of_a_project_out_of_reach_is_404(self):
        other_project = create_project(self.admin, name="Other")
        other_task = create_task(other_project, self.admin, title="Elsewhere")
        self.client.force_authenticate(self.member)
        response = self.client.get(f"/api/v1/projects/tasks/{other_task.uuid}/subtasks")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # ── Create ────────────────────────────────────────────

    def test_member_creates_subtask_appended_at_the_end(self):
        create_subtask(self.task, "existing")
        self.client.force_authenticate(self.member)
        response = self.client.post(self.base_url, {"title": "New item"})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["title"], "New item")
        self.assertEqual(response.data["position"], 1)
        self.assertFalse(response.data["is_done"])

    def test_an_item_can_be_created_already_done(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.base_url, {"title": "Imported", "is_done": True}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["is_done"])
        self.assertTrue(self.task.subtasks.get(title="Imported").done)

    def test_title_is_trimmed(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(self.base_url, {"title": "  padded  "})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["title"], "padded")

    def test_blank_title_is_400(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(self.base_url, {"title": "   "})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_archived_project_rejects_create(self):
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_authenticate(self.member)
        response = self.client.post(self.base_url, {"title": "Nope"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ── Update ────────────────────────────────────────────

    def test_member_toggles_done(self):
        subtask = create_subtask(self.task, "item")
        self.client.force_authenticate(self.member)
        response = self.client.patch(self._detail_url(subtask), {"is_done": True})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        subtask.refresh_from_db()
        self.assertTrue(subtask.done)

    def test_member_renames_subtask(self):
        subtask = create_subtask(self.task, "old title")
        self.client.force_authenticate(self.member)
        response = self.client.patch(self._detail_url(subtask), {"title": "new title"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        subtask.refresh_from_db()
        self.assertEqual(subtask.title, "new title")

    def test_position_is_read_only(self):
        subtask = create_subtask(self.task, "item")
        self.client.force_authenticate(self.member)
        response = self.client.patch(self._detail_url(subtask), {"position": 42})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        subtask.refresh_from_db()
        self.assertEqual(subtask.position, 0)

    def test_archived_project_rejects_update(self):
        subtask = create_subtask(self.task, "item")
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_authenticate(self.member)
        response = self.client.patch(self._detail_url(subtask), {"is_done": True})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_member_reads_one_subtask(self):
        subtask = create_subtask(self.task, "item")
        self.client.force_authenticate(self.member)
        response = self.client.get(self._detail_url(subtask))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["title"], "item")

    def test_subtask_out_of_reach_answers_like_one_that_does_not_exist(self):
        subtask = create_subtask(self.task, "item")
        absent = f"/api/v1/projects/subtasks/{uuid_module.uuid4()}"
        self.client.force_authenticate(self.outsider)
        for method in ("get", "patch", "delete"):
            with self.subTest(method=method):
                hidden = getattr(self.client, method)(self._detail_url(subtask))
                unknown = getattr(self.client, method)(absent)
                self.assertEqual(hidden.status_code, status.HTTP_404_NOT_FOUND)
                self.assertEqual(
                    (hidden.status_code, hidden.content),
                    (unknown.status_code, unknown.content),
                )
        self.assertTrue(self.task.subtasks.filter(pk=subtask.pk).exists())

    # ── Delete ────────────────────────────────────────────

    def test_member_deletes_subtask(self):
        subtask = create_subtask(self.task, "item")
        self.client.force_authenticate(self.member)
        response = self.client.delete(self._detail_url(subtask))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(self.task.subtasks.exists())

    def test_archived_project_rejects_delete(self):
        subtask = create_subtask(self.task, "item")
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_authenticate(self.member)
        response = self.client.delete(self._detail_url(subtask))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ── Reorder ───────────────────────────────────────────

    def test_member_reorders_subtasks(self):
        a = create_subtask(self.task, "a")
        b = create_subtask(self.task, "b")
        c = create_subtask(self.task, "c")
        self.client.force_authenticate(self.member)
        response = self.client.post(
            f"{self.base_url}/reorder",
            {"uuids": [str(c.uuid), str(a.uuid), str(b.uuid)]},
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        titles = [
            s.title for s in self.task.subtasks.order_by("position", "created_at")
        ]
        self.assertEqual(titles, ["c", "a", "b"])

    def test_reorder_malformed_uuid_is_400(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            f"{self.base_url}/reorder", {"uuids": ["not-a-uuid"]}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reorder_duplicate_uuids_is_400(self):
        a = create_subtask(self.task, "a")
        self.client.force_authenticate(self.member)
        response = self.client.post(
            f"{self.base_url}/reorder", {"uuids": [str(a.uuid), str(a.uuid)]}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reorder_refuses_an_item_of_another_task_like_an_unknown_one(self):
        mine = create_subtask(self.task, "mine")
        other_task = create_task(self.project, self.admin, title="Other")
        foreign = create_subtask(other_task, "foreign")
        absent = uuid_module.uuid4()
        self.client.force_authenticate(self.member)

        def reorder(extra):
            response = self.client.post(
                f"{self.base_url}/reorder", {"uuids": [str(extra), str(mine.uuid)]}
            )
            return response.status_code, response.content.replace(
                str(extra).encode(), b"<uuid>"
            )

        refused = reorder(foreign.uuid)
        self.assertEqual(refused[0], status.HTTP_400_BAD_REQUEST)
        self.assertEqual(refused, reorder(absent))

    def test_archived_project_rejects_reorder(self):
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_authenticate(self.member)
        response = self.client.post(
            f"{self.base_url}/reorder", {"uuids": [str(uuid_module.uuid4())]}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
