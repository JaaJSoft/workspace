from datetime import date
from unittest.mock import patch

from rest_framework import status
from rest_framework.test import APITestCase

from workspace.projects.models import Milestone
from workspace.projects.services.milestones import milestones_with_progress
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin


class MilestoneProgressServiceTests(ProjectTestMixin, APITestCase):
    def test_counts_done_and_total_tasks_per_milestone(self):
        beta = Milestone.objects.create(
            project=self.project, name="Beta", target_date=date(2026, 10, 1)
        )
        done = self.project.statuses.get(name="Done")
        for status_ in (None, done):
            task = create_task(self.project, self.admin, title="t", status=status_)
            task.milestone = beta
            task.save(update_fields=["milestone"])
        row = milestones_with_progress(self.project).get()
        self.assertEqual((row.task_count, row.done_task_count), (2, 1))


class MilestoneApiTests(ProjectTestMixin, APITestCase):
    @property
    def url(self):
        return f"/api/v1/projects/{self.project.uuid}/milestones"

    def test_member_lists_milestones_with_rollup_in_date_order(self):
        later = Milestone.objects.create(
            project=self.project, name="GA", target_date=date(2026, 12, 1)
        )
        beta = Milestone.objects.create(
            project=self.project, name="Beta", target_date=date(2026, 10, 1)
        )
        done = self.project.statuses.get(name="Done")
        a = create_task(self.project, self.admin, title="a")
        b = create_task(self.project, self.admin, title="b", status=done)
        for task in (a, b):
            task.milestone = beta
            task.save(update_fields=["milestone"])
        self.client.force_authenticate(self.member)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([m["name"] for m in response.data], ["Beta", "GA"])
        self.assertEqual(response.data[0]["task_count"], 2)
        self.assertEqual(response.data[0]["done_task_count"], 1)
        self.assertEqual(response.data[0]["target_date"], "2026-10-01")
        self.assertFalse(response.data[0]["closed"])
        self.assertEqual(response.data[1]["uuid"], str(later.uuid))

    def test_admin_creates_milestone(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.url,
            {"name": "Beta", "target_date": "2026-10-01", "description": "Ship"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        milestone = self.project.milestones.get()
        self.assertEqual(milestone.description, "Ship")
        self.assertEqual(milestone.target_date, date(2026, 10, 1))
        self.assertIsNone(milestone.closed_at)

    def test_target_date_is_required(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(self.url, {"name": "Beta"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("target_date", response.data)

    def test_member_cannot_create_milestone(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.url, {"name": "Beta", "target_date": "2026-10-01"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_outsider_gets_404(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(
            self.client.get(self.url).status_code, status.HTTP_404_NOT_FOUND
        )

    def test_duplicate_name_is_400(self):
        Milestone.objects.create(
            project=self.project, name="Beta", target_date=date(2026, 10, 1)
        )
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.url, {"name": "Beta", "target_date": "2026-11-01"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_name_race_returns_400(self):
        Milestone.objects.create(
            project=self.project, name="Beta", target_date=date(2026, 10, 1)
        )
        self.client.force_authenticate(self.admin)
        with patch(
            "workspace.projects.serializers.MilestoneSerializer.validate_name",
            side_effect=lambda value: value,
        ):
            response = self.client.post(
                self.url,
                {"name": "Beta", "target_date": "2026-11-01"},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_closes_reopens_moves_and_deletes_milestone(self):
        milestone = Milestone.objects.create(
            project=self.project, name="Beta", target_date=date(2026, 10, 1)
        )
        detail = f"{self.url}/{milestone.uuid}"
        self.client.force_authenticate(self.admin)
        response = self.client.patch(detail, {"closed": True}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["closed"])
        milestone.refresh_from_db()
        self.assertIsNotNone(milestone.closed_at)
        response = self.client.patch(
            detail, {"closed": False, "target_date": "2026-10-15"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        milestone.refresh_from_db()
        self.assertIsNone(milestone.closed_at)
        self.assertEqual(milestone.target_date, date(2026, 10, 15))
        response = self.client.delete(detail)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.project.milestones.count(), 0)

    def test_archived_project_rejects_writes(self):
        from django.utils import timezone

        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.url, {"name": "Beta", "target_date": "2026-10-01"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_deleting_milestone_ungroups_tasks(self):
        milestone = Milestone.objects.create(
            project=self.project, name="Beta", target_date=date(2026, 10, 1)
        )
        task = create_task(self.project, self.admin, title="a")
        task.milestone = milestone
        task.save(update_fields=["milestone"])
        self.client.force_authenticate(self.admin)
        response = self.client.delete(f"{self.url}/{milestone.uuid}")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        task.refresh_from_db()
        self.assertIsNone(task.milestone)


class TaskMilestoneApiTests(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.milestone = Milestone.objects.create(
            project=self.project, name="Beta", target_date=date(2026, 10, 1)
        )

    @property
    def tasks_url(self):
        return f"/api/v1/projects/{self.project.uuid}/tasks"

    def test_create_task_with_milestone_and_dates(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.tasks_url,
            {
                "title": "a",
                "milestone": str(self.milestone.uuid),
                "start_date": "2026-09-01",
                "due_date": "2026-09-10",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["milestone"], self.milestone.uuid)
        self.assertEqual(response.data["start_date"], "2026-09-01")
        task = self.project.tasks.get()
        self.assertEqual(task.milestone, self.milestone)
        self.assertEqual(task.start_date, date(2026, 9, 1))

    def test_start_after_due_is_rejected(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.tasks_url,
            {"title": "a", "start_date": "2026-09-20", "due_date": "2026-09-10"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("start_date", response.data)

    def test_patch_start_after_existing_due_is_rejected(self):
        task = create_task(
            self.project, self.admin, title="a", due_date=date(2026, 9, 10)
        )
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            f"{self.tasks_url}/{task.uuid}", {"start_date": "2026-09-20"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_milestone_from_another_project_is_rejected(self):
        from workspace.projects.services.projects import create_project

        other = create_project(self.admin, name="Other")
        foreign = Milestone.objects.create(
            project=other, name="Elsewhere", target_date=date(2026, 10, 1)
        )
        self.client.force_authenticate(self.member)
        response = self.client.post(
            self.tasks_url,
            {"title": "a", "milestone": str(foreign.uuid)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_milestone_change_records_event_with_names_and_refs(self):
        from workspace.projects.models import TaskEvent

        task = create_task(self.project, self.admin, title="a")
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            f"{self.tasks_url}/{task.uuid}",
            {"milestone": str(self.milestone.uuid)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event = task.events.get(type=TaskEvent.Type.MILESTONE)
        self.assertEqual(event.from_value, "")
        self.assertEqual(event.to_value, "Beta")
        self.assertIsNone(event.from_ref)
        self.assertEqual(event.to_ref, self.milestone.uuid)
        response = self.client.patch(
            f"{self.tasks_url}/{task.uuid}", {"milestone": None}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        latest = task.events.filter(type=TaskEvent.Type.MILESTONE).first()
        self.assertEqual(latest.from_value, "Beta")
        self.assertEqual(latest.to_value, "")
        self.assertEqual(latest.from_ref, self.milestone.uuid)
