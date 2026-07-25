from django.contrib.auth.models import Group
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.projects.models import Project
from workspace.projects.services.projects import get_or_create_personal_project

from .base import ProjectTestMixin


class ProjectListCreateTests(ProjectTestMixin, APITestCase):
    def test_list_shows_my_projects_with_role(self):
        self.client.force_authenticate(self.member)
        response = self.client.get("/api/v1/projects")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["name"], "Website")
        self.assertEqual(response.data[0]["my_role"], "member")

    def test_list_excludes_other_projects(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.get("/api/v1/projects")
        self.assertEqual(response.data, [])

    def test_create_seeds_statuses_and_admin(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.post(
            "/api/v1/projects", {"name": "New", "description": "d"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["my_role"], "admin")
        project = Project.objects.get(uuid=response.data["uuid"])
        self.assertEqual(project.statuses.count(), 4)
        self.assertEqual(project.type, Project.Type.KANBAN)

    def test_create_rejects_group_user_is_not_in(self):
        group = Group.objects.create(name="devs")
        self.client.force_authenticate(self.outsider)
        response = self.client.post(
            "/api/v1/projects",
            {"name": "New", "group": str(group.pk)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_group_only_access_lists_project_as_member(self):
        group = Group.objects.create(name="devs")
        self.outsider.groups.add(group)
        self.project.group = group
        self.project.save(update_fields=["group"])
        self.client.force_authenticate(self.outsider)
        response = self.client.get("/api/v1/projects")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["my_role"], "member")


class ProjectDetailTests(ProjectTestMixin, APITestCase):
    def test_outsider_gets_404(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.get(f"/api/v1/projects/{self.project.uuid}")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_member_cannot_rename(self):
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            f"/api/v1/projects/{self.project.uuid}", {"name": "X"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_renames(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/v1/projects/{self.project.uuid}", {"name": "X"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, "X")

    def test_admin_deletes(self):
        self.client.force_authenticate(self.admin)
        response = self.client.delete(f"/api/v1/projects/{self.project.uuid}")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Project.objects.filter(uuid=self.project.uuid).exists())

    def test_member_cannot_delete(self):
        self.client.force_authenticate(self.member)
        response = self.client.delete(f"/api/v1/projects/{self.project.uuid}")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_deletes_project_containing_tasks(self):
        from workspace.projects.services.tasks import create_task

        create_task(self.project, self.admin, title="t")
        self.client.force_authenticate(self.admin)
        response = self.client.delete(f"/api/v1/projects/{self.project.uuid}")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Project.objects.filter(uuid=self.project.uuid).exists())

    def test_personal_project_cannot_be_deleted_or_archived(self):
        personal = get_or_create_personal_project(self.admin)
        self.client.force_authenticate(self.admin)
        response = self.client.delete(f"/api/v1/projects/{personal.uuid}")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response = self.client.post(f"/api/v1/projects/{personal.uuid}/archive")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_personal_project_cannot_attach_group(self):
        group = Group.objects.create(name="devs")
        self.admin.groups.add(group)
        personal = get_or_create_personal_project(self.admin)
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/v1/projects/{personal.uuid}",
            {"group": str(group.pk)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_archive_and_unarchive(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(f"/api/v1/projects/{self.project.uuid}/archive")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.project.refresh_from_db()
        self.assertTrue(self.project.is_archived)
        response = self.client.post(f"/api/v1/projects/{self.project.uuid}/unarchive")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.project.refresh_from_db()
        self.assertFalse(self.project.is_archived)

    def test_rename_blocked_while_archived(self):
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/v1/projects/{self.project.uuid}", {"name": "X"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
