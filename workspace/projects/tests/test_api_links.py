import uuid as uuid_module

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
        self.base_url = f"/api/v1/projects/tasks/{self.task.uuid}/links"

    def _create(self, target, relation, user=None, anchor=None):
        self.client.force_authenticate(user or self.member)
        url = f"/api/v1/projects/tasks/{(anchor or self.task).uuid}/links"
        return self.client.post(url, {"task": str(target.uuid), "relation": relation})

    def _link_url(self, uuid):
        return f"/api/v1/projects/links/{uuid}"

    # ── List ──────────────────────────────────────────────

    def test_list_describes_each_link_as_the_task_sees_it(self):
        create_link(self.task, self.other, "blocks")
        self.client.force_authenticate(self.member)
        response = self.client.get(self.base_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        (item,) = response.data
        self.assertEqual(set(item), {"uuid", "relation", "label", "task"})
        self.assertEqual((item["relation"], item["label"]), ("blocks", "blocks"))
        self.assertEqual(
            item["task"],
            {
                "uuid": str(self.other.uuid),
                "reference": self.other.reference,
                "title": "Other",
                "url": self.other.get_absolute_url(),
                "project": {
                    "uuid": str(self.project.uuid),
                    "name": "Website",
                    "key": self.project.key,
                },
                "is_done": False,
            },
        )

    def test_the_other_end_reads_the_relation_the_other_way(self):
        create_link(self.task, self.other, "blocks")
        create_link(self.task, self.other, "duplicates")
        create_link(self.task, self.other, "relates_to")
        self.client.force_authenticate(self.member)
        response = self.client.get(f"/api/v1/projects/tasks/{self.other.uuid}/links")
        self.assertEqual(
            [(item["relation"], item["label"]) for item in response.data],
            [
                ("blocked_by", "is blocked by"),
                ("duplicated_by", "is duplicated by"),
                ("relates_to", "relates to"),
            ],
        )

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

    def test_member_creates_a_link_and_gets_it_back(self):
        response = self._create(self.other, "blocked_by")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        link = TaskLink.objects.get()
        self.assertEqual(link.source, self.other)
        self.assertEqual(link.target, self.task)
        self.assertEqual(response.data["uuid"], str(link.uuid))
        self.assertEqual(response.data["relation"], "blocked_by")
        self.assertEqual(response.data["label"], "is blocked by")
        self.assertEqual(response.data["task"]["uuid"], str(self.other.uuid))

    def test_create_records_events_on_both_tasks(self):
        self._create(self.other, "blocks")
        self.assertTrue(
            self.task.events.filter(
                type=TaskEvent.Type.LINKED, actor=self.member
            ).exists()
        )
        self.assertTrue(self.other.events.filter(type=TaskEvent.Type.LINKED).exists())

    def test_a_task_out_of_reach_answers_like_one_that_does_not_exist(self):
        secret_project = create_project(self.admin, name="Secret")
        secret = create_task(secret_project, self.admin, title="Hidden")
        frozen_project = create_project(self.admin, name="Frozen")
        frozen_project.members.create(user=self.member)
        frozen = create_task(frozen_project, self.admin, title="Elsewhere")
        frozen_project.archived_at = timezone.now()
        frozen_project.save(update_fields=["archived_at"])
        absent = uuid_module.uuid4()
        self.client.force_authenticate(self.member)

        def link_to(target):
            response = self.client.post(
                self.base_url, {"task": str(target), "relation": "relates_to"}
            )
            return response.status_code, response.content.replace(
                str(target).encode(), b"<uuid>"
            )

        unknown = link_to(absent)
        self.assertEqual(unknown[0], status.HTTP_400_BAD_REQUEST)
        self.assertEqual(link_to(secret.uuid), unknown)
        # A project that takes no change anymore names no task to link.
        self.assertEqual(link_to(frozen.uuid), unknown)
        self.assertFalse(TaskLink.objects.exists())

    def test_cross_project_link_works_when_both_are_accessible(self):
        other_project = create_project(self.admin, name="Other project")
        other_task = create_task(other_project, self.admin, title="Elsewhere")
        response = self._create(other_task, "blocks", user=self.admin)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["task"]["project"]["name"], "Other project")

    def test_duplicate_link_is_a_409(self):
        create_link(self.task, self.other, "blocks")
        response = self._create(self.other, "blocks")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("detail", response.data)

    def test_block_cycle_is_a_409(self):
        third = create_task(self.project, self.admin, title="Third")
        create_link(self.task, self.other, "blocks")
        create_link(self.other, third, "blocks")
        response = self._create(self.task, "blocks", anchor=third)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(TaskLink.objects.count(), 2)

    def test_self_link_is_a_400_on_the_task(self):
        response = self._create(self.task, "relates_to")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(list(response.data), ["task"])

    def test_unknown_relation_is_a_400(self):
        response = self._create(self.other, "mentions")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(list(response.data), ["relation"])

    def test_archived_project_rejects_create(self):
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        response = self._create(self.other, "blocks")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ── Destroy ───────────────────────────────────────────

    def test_member_removes_a_link(self):
        link = create_link(self.other, self.task, "blocks")
        self.client.force_authenticate(self.member)
        response = self.client.delete(self._link_url(link.uuid))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(TaskLink.objects.exists())
        self.assertTrue(self.task.events.filter(type=TaskEvent.Type.UNLINKED).exists())

    def test_a_link_is_removed_from_the_end_the_caller_reaches(self):
        elsewhere = create_project(self.admin, name="Elsewhere")
        far = create_task(elsewhere, self.admin, title="Far")
        link = create_link(far, self.task, "blocks")
        # The member reaches one end only.
        self.client.force_authenticate(self.member)
        response = self.client.delete(self._link_url(link.uuid))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(TaskLink.objects.exists())

    def test_a_link_out_of_reach_answers_like_one_that_does_not_exist(self):
        link = create_link(self.task, self.other, "blocks")
        self.client.force_authenticate(self.outsider)
        hidden = self.client.delete(self._link_url(link.uuid))
        unknown = self.client.delete(self._link_url(uuid_module.uuid4()))
        self.assertEqual(hidden.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            (hidden.status_code, hidden.content), (unknown.status_code, unknown.content)
        )
        self.assertTrue(TaskLink.objects.exists())

    def test_a_link_between_archived_projects_cannot_be_removed(self):
        link = create_link(self.task, self.other, "blocks")
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_authenticate(self.member)
        response = self.client.delete(self._link_url(link.uuid))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(TaskLink.objects.exists())

    def test_one_end_in_an_open_project_is_enough_to_remove_a_link(self):
        open_project = create_project(self.admin, name="Open")
        near = create_task(open_project, self.admin, title="Near")
        link = create_link(self.task, near, "relates_to")
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_authenticate(self.admin)
        response = self.client.delete(self._link_url(link.uuid))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
