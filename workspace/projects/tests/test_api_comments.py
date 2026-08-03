from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.notifications.models import Notification
from workspace.projects.models import TaskEvent
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
        comments = response.data["comments"]
        self.assertEqual([c["body"] for c in comments], ["first", "second"])
        self.assertEqual(comments[0]["author"]["username"], "admin1")

    def test_list_includes_mention_users(self):
        self.client.force_authenticate(self.member)
        response = self.client.get(self.base_url)
        usernames = {u["username"] for u in response.data["mention_users"]}
        self.assertIn("admin1", usernames)
        self.assertIn("member1", usernames)
        self.assertEqual(
            set(response.data["mention_users"][0].keys()),
            {"id", "username", "first_name", "last_name"},
        )

    def test_soft_deleted_comments_are_hidden_from_list(self):
        comment = self._create_comment(self.admin)
        comment.deleted_at = timezone.now()
        comment.save(update_fields=["deleted_at"])
        self.client.force_authenticate(self.member)
        response = self.client.get(self.base_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["comments"], [])

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


class TaskCommentMentionTests(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Ship the thing")
        self.base_url = (
            f"/api/v1/projects/{self.project.uuid}/tasks/{self.task.uuid}/comments"
        )

    def _notifs_for(self, user):
        return Notification.objects.filter(recipient=user, origin="projects")

    def test_mentioned_member_gets_high_priority_notification(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.base_url, {"body": "ping @member1"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        notifs = self._notifs_for(self.member)
        self.assertEqual(notifs.count(), 1)
        n = notifs.first()
        self.assertIn("mentioned", n.title)
        self.assertEqual(n.priority, "high")
        self.assertEqual(n.actor, self.admin)

    def test_mentioned_watcher_not_doubly_notified(self):
        """The task creator, when mentioned, gets only the mention notif."""
        self.client.force_authenticate(self.member)
        self.client.post(self.base_url, {"body": "wdyt @admin1"}, format="json")
        notifs = self._notifs_for(self.admin)
        self.assertEqual(notifs.count(), 1)
        self.assertIn("mentioned", notifs.first().title)

    def test_mention_of_non_member_is_ignored(self):
        self.client.force_authenticate(self.admin)
        self.client.post(self.base_url, {"body": "hey @outsider1"}, format="json")
        self.assertEqual(self._notifs_for(self.outsider).count(), 0)

    def test_self_mention_no_notification(self):
        self.client.force_authenticate(self.admin)
        self.client.post(self.base_url, {"body": "note @admin1"}, format="json")
        self.assertEqual(self._notifs_for(self.admin).count(), 0)

    def test_body_html_renders_mention_badge(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.base_url, {"body": "ping @member1"}, format="json"
        )
        self.assertIn("mention-badge", response.data["body_html"])
        self.assertIn(f'data-user-id="{self.member.pk}"', response.data["body_html"])

    def test_body_html_escapes_html(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.base_url, {"body": "<script>x</script>"}, format="json"
        )
        self.assertNotIn("<script>", response.data["body_html"])

    def test_create_records_commented_event(self):
        self.client.force_authenticate(self.member)
        self.client.post(self.base_url, {"body": "done"}, format="json")
        event = self.task.events.filter(type=TaskEvent.Type.COMMENTED).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.actor, self.member)

    def test_edit_notifies_newly_mentioned_only(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(self.base_url, {"body": "draft"}, format="json")
        comment_uuid = response.data["uuid"]
        self.client.patch(
            f"{self.base_url}/{comment_uuid}",
            {"body": "draft, ping @member1"},
            format="json",
        )
        notifs = self._notifs_for(self.member)
        self.assertEqual(notifs.count(), 1)
        self.assertIn("mentioned", notifs.first().title)

    def test_edit_does_not_renotify_existing_mention(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.base_url, {"body": "ping @member1"}, format="json"
        )
        comment_uuid = response.data["uuid"]
        self.client.patch(
            f"{self.base_url}/{comment_uuid}",
            {"body": "ping @member1 again"},
            format="json",
        )
        self.assertEqual(self._notifs_for(self.member).count(), 1)
