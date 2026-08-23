from rest_framework import status
from rest_framework.test import APITestCase

from workspace.projects.models import TaskEvent
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin


class EpicApiTests(ProjectTestMixin, APITestCase):
    def test_member_lists_epics_with_rollup(self):
        epic = self.project.epics.create(name="Launch", color="#3b82f6")
        done = self.project.statuses.get(name="Done")
        create_task(self.project, self.admin, title="a", epic=epic)
        create_task(self.project, self.admin, title="b", epic=epic, status=done)
        self.client.force_authenticate(self.member)
        response = self.client.get(f"/api/v1/projects/{self.project.uuid}/epics")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]["name"], "Launch")
        self.assertEqual(response.data[0]["task_count"], 2)
        self.assertEqual(response.data[0]["done_task_count"], 1)
        self.assertFalse(response.data[0]["closed"])

    def test_admin_creates_epic(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            f"/api/v1/projects/{self.project.uuid}/epics",
            {"name": "Launch", "color": "#3b82f6", "description": "Ship v1"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.project.epics.count(), 1)
        epic = self.project.epics.get()
        self.assertEqual(epic.description, "Ship v1")
        self.assertIsNone(epic.closed_at)

    def test_member_cannot_create_epic(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            f"/api/v1/projects/{self.project.uuid}/epics",
            {"name": "Launch"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_outsider_gets_404(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.get(f"/api/v1/projects/{self.project.uuid}/epics")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_duplicate_name_is_400(self):
        self.project.epics.create(name="Launch")
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            f"/api/v1/projects/{self.project.uuid}/epics",
            {"name": "Launch"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_name_race_returns_400(self):
        from unittest.mock import patch

        self.project.epics.create(name="Launch")
        self.client.force_authenticate(self.admin)
        with patch(
            "workspace.projects.serializers.EpicSerializer.validate_name",
            side_effect=lambda value: value,
        ):
            response = self.client.post(
                f"/api/v1/projects/{self.project.uuid}/epics",
                {"name": "Launch"},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_closes_reopens_and_deletes_epic(self):
        epic = self.project.epics.create(name="Launch")
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/v1/projects/{self.project.uuid}/epics/{epic.uuid}",
            {"closed": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["closed"])
        epic.refresh_from_db()
        self.assertIsNotNone(epic.closed_at)
        response = self.client.patch(
            f"/api/v1/projects/{self.project.uuid}/epics/{epic.uuid}",
            {"closed": False},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        epic.refresh_from_db()
        self.assertIsNone(epic.closed_at)
        response = self.client.delete(
            f"/api/v1/projects/{self.project.uuid}/epics/{epic.uuid}"
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.project.epics.count(), 0)

    def test_deleting_epic_ungroups_tasks(self):
        epic = self.project.epics.create(name="Launch")
        task = create_task(self.project, self.admin, title="a", epic=epic)
        self.client.force_authenticate(self.admin)
        response = self.client.delete(
            f"/api/v1/projects/{self.project.uuid}/epics/{epic.uuid}"
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        task.refresh_from_db()
        self.assertIsNone(task.epic)


class TaskEpicApiTests(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.epic = self.project.epics.create(name="Launch")

    def _task_url(self, task):
        return f"/api/v1/projects/{self.project.uuid}/tasks/{task.uuid}"

    def test_create_task_with_epic(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            f"/api/v1/projects/{self.project.uuid}/tasks",
            {"title": "a", "epic": str(self.epic.uuid)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["epic"], self.epic.uuid)
        task = self.project.tasks.get()
        self.assertEqual(task.epic, self.epic)

    def test_epic_from_another_project_is_rejected(self):
        other = create_project(self.admin, name="Other")
        foreign = other.epics.create(name="Elsewhere")
        self.client.force_authenticate(self.member)
        response = self.client.post(
            f"/api/v1/projects/{self.project.uuid}/tasks",
            {"title": "a", "epic": str(foreign.uuid)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_setting_epic_records_event_with_name_snapshots(self):
        task = create_task(self.project, self.member, title="a")
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            self._task_url(task), {"epic": str(self.epic.uuid)}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event = task.events.filter(type=TaskEvent.Type.EPIC).get()
        self.assertEqual(event.from_value, "")
        self.assertEqual(event.to_value, "Launch")
        # The dedicated EPIC event replaces the generic UPDATED one.
        self.assertFalse(task.events.filter(type=TaskEvent.Type.UPDATED).exists())

    def test_changing_and_clearing_epic_record_events(self):
        task = create_task(self.project, self.member, title="a", epic=self.epic)
        other = self.project.epics.create(name="Polish")
        self.client.force_authenticate(self.member)
        self.client.patch(
            self._task_url(task), {"epic": str(other.uuid)}, format="json"
        )
        event = task.events.filter(type=TaskEvent.Type.EPIC).latest("created_at")
        self.assertEqual(event.from_value, "Launch")
        self.assertEqual(event.to_value, "Polish")
        self.client.patch(self._task_url(task), {"epic": None}, format="json")
        event = (
            task.events.filter(type=TaskEvent.Type.EPIC)
            .order_by("-created_at", "-uuid")
            .first()
        )
        self.assertEqual(event.from_value, "Polish")
        self.assertEqual(event.to_value, "")

    def test_noop_epic_patch_records_nothing(self):
        task = create_task(self.project, self.member, title="a", epic=self.epic)
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            self._task_url(task), {"epic": str(self.epic.uuid)}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(task.events.filter(type=TaskEvent.Type.EPIC).exists())

    def test_list_filters_by_epic(self):
        create_task(self.project, self.admin, title="inside", epic=self.epic)
        create_task(self.project, self.admin, title="outside")
        self.client.force_authenticate(self.member)
        response = self.client.get(
            f"/api/v1/projects/{self.project.uuid}/tasks",
            {"epic": str(self.epic.uuid)},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([t["title"] for t in response.data], ["inside"])

    def test_malformed_epic_filter_is_400(self):
        self.client.force_authenticate(self.member)
        response = self.client.get(
            f"/api/v1/projects/{self.project.uuid}/tasks", {"epic": "not-a-uuid"}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
