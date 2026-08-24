from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.projects.models import Project, Sprint
from workspace.projects.services.projects import (
    create_project,
    get_or_create_personal_project,
)
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin


class ConvertApiTestCase(ProjectTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.url = f"/api/v1/projects/{self.project.uuid}/convert"

    def convert(self, user, project_type, project=None):
        self.client.force_authenticate(user)
        url = self.url
        if project is not None:
            url = f"/api/v1/projects/{project.uuid}/convert"
        return self.client.post(url, {"type": project_type}, format="json")


class ConvertApiTests(ConvertApiTestCase):
    def test_admin_converts_to_scrum(self):
        doing = self.project.statuses.get(name="In progress")
        task = create_task(self.project, self.admin, title="wip", status=doing)

        response = self.convert(self.admin, "scrum")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["type"], "scrum")
        self.project.refresh_from_db()
        self.assertEqual(self.project.type, Project.Type.SCRUM)
        task.refresh_from_db()
        self.assertEqual(task.sprint_id, self.project.sprints.get().pk)

    def test_admin_converts_back_to_kanban(self):
        scrum = create_project(
            self.admin, name="Rocket", project_type=Project.Type.SCRUM
        )
        sprint = scrum.sprints.create(name="Sprint 1", state=Sprint.State.ACTIVE)

        response = self.convert(self.admin, "kanban", project=scrum)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        scrum.refresh_from_db()
        sprint.refresh_from_db()
        self.assertEqual(scrum.type, Project.Type.KANBAN)
        self.assertEqual(sprint.state, Sprint.State.CLOSED)

    def test_member_cannot_convert(self):
        response = self.convert(self.member, "scrum")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.project.refresh_from_db()
        self.assertEqual(self.project.type, Project.Type.KANBAN)

    def test_outsider_gets_404(self):
        response = self.convert(self.outsider, "scrum")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_archived_project_is_read_only(self):
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        response = self.convert(self.admin, "scrum")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_personal_project_is_rejected(self):
        personal = get_or_create_personal_project(self.admin)
        response = self.convert(self.admin, "scrum", project=personal)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        personal.refresh_from_db()
        self.assertEqual(personal.type, Project.Type.PERSONAL)

    def test_personal_target_is_rejected(self):
        response = self.convert(self.admin, "personal")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unknown_type_is_rejected(self):
        response = self.convert(self.admin, "waterfall")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_converting_to_the_current_type_changes_nothing(self):
        response = self.convert(self.admin, "kanban")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.project.sprints.count(), 0)


class TypeFieldImmutabilityTests(ConvertApiTestCase):
    def test_patching_the_type_is_refused(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/v1/projects/{self.project.uuid}",
            {"type": "scrum"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.project.refresh_from_db()
        self.assertEqual(self.project.type, Project.Type.KANBAN)

    def test_patching_the_unchanged_type_is_accepted(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/v1/projects/{self.project.uuid}",
            {"type": "kanban", "name": "Website 2"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class SprintsRequireScrumTests(ConvertApiTestCase):
    def test_sprints_cannot_be_created_on_a_kanban_project(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            f"/api/v1/projects/{self.project.uuid}/sprints",
            {"name": "Sprint 1"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.project.sprints.count(), 0)

    def test_history_left_by_a_conversion_cannot_be_deleted(self):
        scrum = create_project(
            self.admin, name="Rocket", project_type=Project.Type.SCRUM
        )
        sprint = scrum.sprints.create(name="Sprint 1", state=Sprint.State.ACTIVE)
        self.convert(self.admin, "kanban", project=scrum)

        response = self.client.delete(
            f"/api/v1/projects/{scrum.uuid}/sprints/{sprint.uuid}"
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(scrum.sprints.filter(pk=sprint.pk).exists())
