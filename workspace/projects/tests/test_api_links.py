from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.projects.models import TaskEvent, TaskLink
from workspace.projects.services.links import create_link
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin


class TaskLinkApiTests(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Anchor")
        self.other = create_task(self.project, self.admin, title="Other")
        self.base_url = (
            f"/api/v1/projects/{self.project.uuid}/tasks/{self.task.uuid}/links"
        )

    def _create(self, target, relation, user=None):
        self.client.force_authenticate(user or self.member)
        return self.client.post(
            self.base_url, {"target": str(target.uuid), "relation": relation}
        )

    # ── List ──────────────────────────────────────────────

    def test_list_serializes_links_relative_to_the_anchor(self):
        create_link(self.task, self.other, "blocks")
        self.client.force_authenticate(self.member)
        response = self.client.get(self.base_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        (item,) = response.data
        self.assertEqual(item["label"], "blocks")
        self.assertEqual(item["type"], "blocks")
        self.assertEqual(item["task"]["uuid"], str(self.other.uuid))
        self.assertEqual(item["task"]["reference"], self.other.reference)

    def test_list_hides_links_into_inaccessible_projects(self):
        secret_project = create_project(self.admin, name="Secret")
        secret = create_task(secret_project, self.admin, title="Hidden")
        create_link(self.task, secret, "relates_to")
        self.client.force_authenticate(self.member)
        response = self.client.get(self.base_url)
        self.assertEqual(response.data, [])

    def test_outsider_gets_404(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.get(self.base_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # ── Create ────────────────────────────────────────────

    def test_member_creates_a_link_and_gets_the_updated_list(self):
        response = self._create(self.other, "blocked_by")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        link = TaskLink.objects.get()
        self.assertEqual(link.source, self.other)
        self.assertEqual(link.target, self.task)
        (item,) = response.data
        self.assertEqual(item["label"], "is blocked by")

    def test_create_records_events_on_both_tasks(self):
        self._create(self.other, "blocks")
        self.assertTrue(
            self.task.events.filter(
                type=TaskEvent.Type.LINKED, actor=self.member
            ).exists()
        )
        self.assertTrue(self.other.events.filter(type=TaskEvent.Type.LINKED).exists())

    def test_cross_project_link_requires_access_to_the_target(self):
        secret_project = create_project(self.admin, name="Secret")
        secret = create_task(secret_project, self.admin, title="Hidden")
        response = self._create(secret, "relates_to")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(TaskLink.objects.exists())

    def test_cross_project_link_works_when_both_are_accessible(self):
        other_project = create_project(self.admin, name="Other project")
        other_task = create_task(other_project, self.admin, title="Elsewhere")
        response = self._create(other_task, "blocks", user=self.admin)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_archived_target_project_is_treated_as_not_found(self):
        other_project = create_project(self.admin, name="Frozen")
        other_task = create_task(other_project, self.admin, title="Elsewhere")
        other_project.archived_at = timezone.now()
        other_project.save(update_fields=["archived_at"])
        response = self._create(other_task, "blocks", user=self.admin)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_duplicate_link_is_a_400(self):
        create_link(self.task, self.other, "blocks")
        response = self._create(self.other, "blocks")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_block_cycle_is_a_400(self):
        third = create_task(self.project, self.admin, title="Third")
        create_link(self.task, self.other, "blocks")
        create_link(self.other, third, "blocks")
        url = f"/api/v1/projects/{self.project.uuid}/tasks/{third.uuid}/links"
        self.client.force_authenticate(self.member)
        response = self.client.post(
            url, {"target": str(self.task.uuid), "relation": "blocks"}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(TaskLink.objects.count(), 2)

    def test_self_link_is_a_400(self):
        response = self._create(self.task, "relates_to")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unknown_relation_is_a_400(self):
        response = self._create(self.other, "mentions")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_archived_project_rejects_create(self):
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        response = self._create(self.other, "blocks")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ── Destroy ───────────────────────────────────────────

    def test_member_removes_a_link_from_either_end(self):
        link = create_link(self.other, self.task, "blocks")
        self.client.force_authenticate(self.member)
        response = self.client.delete(f"{self.base_url}/{link.uuid}")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(TaskLink.objects.exists())
        self.assertTrue(self.task.events.filter(type=TaskEvent.Type.UNLINKED).exists())

    def test_destroy_rejects_a_link_not_touching_the_anchor(self):
        third = create_task(self.project, self.admin, title="Third")
        link = create_link(self.other, third, "relates_to")
        self.client.force_authenticate(self.member)
        response = self.client.delete(f"{self.base_url}/{link.uuid}")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(TaskLink.objects.exists())
