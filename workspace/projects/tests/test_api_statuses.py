from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.projects.models import TaskStatus
from workspace.projects.services.projects import get_or_create_personal_project
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin


class StatusCreateApiTests(ProjectTestMixin, APITestCase):
    def _url(self):
        return f"/api/v1/projects/{self.project.uuid}/statuses"

    def test_admin_creates_column_at_end(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self._url(),
            {"name": "Review", "category": "active", "color": "#22c55e"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["position"], 4)
        self.assertEqual(response.data["color"], "#22c55e")

    def test_member_cannot_create_column(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self._url(), {"name": "Review", "category": "active"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_outsider_gets_404(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.post(
            self._url(), {"name": "Review", "category": "active"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_duplicate_name_is_400(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self._url(), {"name": "Done", "category": "done"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_name_race_returns_400(self):
        from unittest.mock import patch

        self.client.force_authenticate(self.admin)
        with patch(
            "workspace.projects.serializers.TaskStatusSerializer.validate_name",
            side_effect=lambda value: value,
        ):
            response = self.client.post(
                self._url(), {"name": "Done", "category": "done"}, format="json"
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_archived_project_is_403(self):
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self._url(), {"name": "Review", "category": "active"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_personal_project_creator_can_create(self):
        personal = get_or_create_personal_project(self.member)
        self.client.force_authenticate(self.member)
        response = self.client.post(
            f"/api/v1/projects/{personal.uuid}/statuses",
            {"name": "Review", "category": "active"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class StatusUpdateApiTests(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.todo = self.project.statuses.get(name="To do")

    def _url(self, uuid):
        return f"/api/v1/projects/{self.project.uuid}/statuses/{uuid}"

    def test_admin_renames_and_recolors(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            self._url(self.todo.uuid),
            {"name": "Todo!", "color": "#f97316"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.todo.refresh_from_db()
        self.assertEqual(self.todo.name, "Todo!")
        self.assertEqual(self.todo.color, "#f97316")

    def test_category_is_immutable(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            self._url(self.todo.uuid), {"category": "done"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rename_to_existing_name_is_400(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            self._url(self.todo.uuid), {"name": "Done"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_member_cannot_update(self):
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            self._url(self.todo.uuid), {"name": "X"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class StatusDeleteApiTests(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.todo = self.project.statuses.get(name="To do")
        self.in_progress = self.project.statuses.get(name="In progress")
        self.backlog = self.project.statuses.get(name="Backlog")

    def _url(self, uuid, move_to=None):
        url = f"/api/v1/projects/{self.project.uuid}/statuses/{uuid}"
        if move_to is not None:
            url += f"?move_to={move_to}"
        return url

    def test_admin_deletes_empty_column(self):
        self.client.force_authenticate(self.admin)
        response = self.client.delete(self._url(self.todo.uuid))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_delete_with_tasks_requires_move_to(self):
        create_task(self.project, self.admin, title="A", status=self.todo)
        self.client.force_authenticate(self.admin)
        response = self.client.delete(self._url(self.todo.uuid))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_malformed_move_to_is_400(self):
        self.client.force_authenticate(self.admin)
        response = self.client.delete(self._url(self.todo.uuid, move_to="not-a-uuid"))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unknown_move_to_is_400(self):
        import uuid as uuid_module

        self.client.force_authenticate(self.admin)
        response = self.client.delete(
            self._url(self.todo.uuid, move_to=uuid_module.uuid4())
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_delete_moves_tasks_to_target(self):
        task = create_task(self.project, self.admin, title="A", status=self.todo)
        self.client.force_authenticate(self.admin)
        response = self.client.delete(
            self._url(self.todo.uuid, move_to=self.in_progress.uuid)
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        task.refresh_from_db()
        self.assertEqual(task.status, self.in_progress)

    def test_last_category_column_is_400(self):
        self.client.force_authenticate(self.admin)
        response = self.client.delete(self._url(self.backlog.uuid))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("last", response.data["detail"])

    def test_member_cannot_delete(self):
        self.client.force_authenticate(self.member)
        response = self.client.delete(self._url(self.todo.uuid))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class StatusReorderApiTests(ProjectTestMixin, APITestCase):
    def _url(self):
        return f"/api/v1/projects/{self.project.uuid}/statuses/reorder"

    def test_admin_reorders(self):
        statuses = {s.name: s for s in self.project.statuses.all()}
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self._url(),
            {
                "order": [
                    str(statuses["Done"].uuid),
                    str(statuses["Backlog"].uuid),
                    str(statuses["To do"].uuid),
                    str(statuses["In progress"].uuid),
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = list(
            self.project.statuses.order_by("position", "created_at").values_list(
                "name", flat=True
            )
        )
        self.assertEqual(names, ["Done", "Backlog", "To do", "In progress"])

    def test_invalid_uuid_in_order_is_400(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(self._url(), {"order": ["nope"]}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_member_cannot_reorder(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(self._url(), {"order": []}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class StatusListStillWorksTests(ProjectTestMixin, APITestCase):
    def test_member_lists_statuses_in_order(self):
        self.client.force_authenticate(self.member)
        response = self.client.get(f"/api/v1/projects/{self.project.uuid}/statuses")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [s["name"] for s in response.data],
            ["Backlog", "To do", "In progress", "Done"],
        )
