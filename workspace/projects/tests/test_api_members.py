from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.projects.models import ProjectMember

from .base import ProjectTestMixin

User = get_user_model()


class MemberListTests(ProjectTestMixin, APITestCase):
    def test_member_lists_active_members(self):
        self.client.force_authenticate(self.member)
        response = self.client.get(f"/api/v1/projects/{self.project.uuid}/members")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual({m["username"] for m in response.data}, {"admin1", "member1"})

    def test_outsider_gets_404(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.get(f"/api/v1/projects/{self.project.uuid}/members")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class MemberCreateTests(ProjectTestMixin, APITestCase):
    def test_admin_invites_user(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            f"/api/v1/projects/{self.project.uuid}/members",
            {"user": str(self.outsider.pk), "role": "member"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            ProjectMember.objects.filter(
                project=self.project, user=self.outsider, left_at__isnull=True
            ).exists()
        )

    def test_member_cannot_invite(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            f"/api/v1/projects/{self.project.uuid}/members",
            {"user": str(self.outsider.pk)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_nonexistent_user_is_400(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            f"/api/v1/projects/{self.project.uuid}/members",
            {"user": 999999},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], "User not found.")

    def test_malformed_user_id_is_400(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            f"/api/v1/projects/{self.project.uuid}/members",
            {"user": "not-an-id"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class MemberUpdateDeleteTests(ProjectTestMixin, APITestCase):
    def test_admin_promotes_member(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/v1/projects/{self.project.uuid}/members/{self.membership.uuid}",
            {"role": "admin"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.role, ProjectMember.Role.ADMIN)

    def test_demoting_last_admin_is_400(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/v1/projects/{self.project.uuid}/members/{self.admin_membership.uuid}",
            {"role": "member"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_removes_member(self):
        self.client.force_authenticate(self.admin)
        response = self.client.delete(
            f"/api/v1/projects/{self.project.uuid}/members/{self.membership.uuid}"
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.membership.refresh_from_db()
        self.assertIsNotNone(self.membership.left_at)

    def test_member_leaves_own_row(self):
        self.client.force_authenticate(self.member)
        response = self.client.delete(
            f"/api/v1/projects/{self.project.uuid}/members/{self.membership.uuid}"
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_member_cannot_remove_someone_else(self):
        self.client.force_authenticate(self.member)
        response = self.client.delete(
            f"/api/v1/projects/{self.project.uuid}/members/{self.admin_membership.uuid}"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_last_admin_cannot_leave(self):
        self.client.force_authenticate(self.admin)
        response = self.client.delete(
            f"/api/v1/projects/{self.project.uuid}/members/{self.admin_membership.uuid}"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ArchivedProjectMemberTests(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])

    def test_mutations_blocked_while_archived(self):
        self.client.force_authenticate(self.admin)
        base = f"/api/v1/projects/{self.project.uuid}/members"
        response = self.client.post(base, {"user": self.outsider.pk}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        response = self.client.patch(
            f"{base}/{self.membership.uuid}", {"role": "admin"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        response = self.client.delete(f"{base}/{self.admin_membership.uuid}")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_still_allowed_while_archived(self):
        self.client.force_authenticate(self.member)
        response = self.client.get(f"/api/v1/projects/{self.project.uuid}/members")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
