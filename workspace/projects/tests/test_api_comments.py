from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.notifications.models import Notification
from workspace.projects.services.members import add_member
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin

User = get_user_model()


class TaskCommentApiTests(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Ship the thing")
        self.base_url = (
            f"/api/v1/projects/{self.project.uuid}/tasks/{self.task.uuid}/comments"
        )

    def _create_comment(self, author, body="hello"):
        return self.task.comments.create(author=author, body=body)

    # ── List ──────────────────────────────────────────────

    def test_member_lists_comments_in_creation_order(self):
        self._create_comment(self.admin, body="first")
        self._create_comment(self.member, body="second")
        self.client.force_authenticate(self.member)
        response = self.client.get(self.base_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([c["body"] for c in response.data], ["first", "second"])
        self.assertEqual(response.data[0]["author"]["username"], "admin1")

    def test_soft_deleted_comments_are_hidden_from_list(self):
        comment = self._create_comment(self.admin)
        comment.deleted_at = timezone.now()
        comment.save(update_fields=["deleted_at"])
        self.client.force_authenticate(self.member)
        response = self.client.get(self.base_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_outsider_gets_404(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.get(self.base_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_task_from_another_project_is_404(self):
        other_project = create_project(self.admin, name="Other")
        other_task = create_task(other_project, self.admin, title="Elsewhere")
        self.client.force_authenticate(self.member)
        response = self.client.get(
            f"/api/v1/projects/{self.project.uuid}/tasks/{other_task.uuid}/comments"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # ── Create ────────────────────────────────────────────

    def test_member_creates_comment(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(self.base_url, {"body": "on it"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["body"], "on it")
        self.assertEqual(response.data["author"]["username"], "member1")
        self.assertEqual(self.task.comments.count(), 1)

    def test_empty_body_is_400(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(self.base_url, {"body": ""}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_on_archived_project_is_403(self):
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_authenticate(self.member)
        response = self.client.post(self.base_url, {"body": "late"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_comment_notifies_creator_assignees_and_commenters_except_actor(self):
        self.task.assignees.add(self.member)
        self._create_comment(self.member, body="earlier remark")
        commenter = User.objects.create_user(
            username="third1", email="third1@test.com", password="pass123"
        )
        add_member(self.project, commenter)
        self._create_comment(commenter, body="me too")

        self.client.force_authenticate(self.member)
        response = self.client.post(self.base_url, {"body": "done"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        recipients = set(
            Notification.objects.filter(origin="projects").values_list(
                "recipient__username", flat=True
            )
        )
        # admin (task creator) + third1 (prior commenter); member is the actor.
        self.assertEqual(recipients, {"admin1", "third1"})

    # ── Edit ──────────────────────────────────────────────

    def test_author_edits_own_comment(self):
        comment = self._create_comment(self.member, body="typo")
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            f"{self.base_url}/{comment.uuid}", {"body": "fixed"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        comment.refresh_from_db()
        self.assertEqual(comment.body, "fixed")
        self.assertIsNotNone(comment.edited_at)

    def test_non_author_cannot_edit(self):
        comment = self._create_comment(self.member)
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"{self.base_url}/{comment.uuid}", {"body": "hijack"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_edit_unknown_comment_is_404(self):
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            f"{self.base_url}/{self.task.uuid}", {"body": "x"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # ── Delete ────────────────────────────────────────────

    def test_author_soft_deletes_own_comment(self):
        comment = self._create_comment(self.member)
        self.client.force_authenticate(self.member)
        response = self.client.delete(f"{self.base_url}/{comment.uuid}")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        comment.refresh_from_db()
        self.assertIsNotNone(comment.deleted_at)

    def test_non_author_cannot_delete(self):
        comment = self._create_comment(self.member)
        self.client.force_authenticate(self.admin)
        response = self.client.delete(f"{self.base_url}/{comment.uuid}")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
