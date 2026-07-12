from django.test import TestCase

from workspace.projects.models import Project
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin


class IndexViewTests(ProjectTestMixin, TestCase):
    def test_index_lazily_creates_personal_project_and_lists(self):
        self.client.force_login(self.member)
        response = self.client.get("/projects")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "projects/ui/index.html")
        self.assertTrue(
            Project.objects.filter(
                created_by=self.member, type=Project.Type.PERSONAL
            ).exists()
        )
        self.assertContains(response, "Website")
        self.assertContains(response, "Personal")

    def test_alpine_request_returns_partial(self):
        self.client.force_login(self.member)
        response = self.client.get("/projects", HTTP_X_ALPINE_REQUEST="1")
        self.assertTemplateUsed(response, "projects/ui/partials/project_list.html")

    def test_anonymous_redirected_to_login(self):
        response = self.client.get("/projects")
        self.assertEqual(response.status_code, 302)


class ProjectViewTests(ProjectTestMixin, TestCase):
    def test_renders_board_columns_and_backlog(self):
        create_task(self.project, self.admin, title="Fix the login flow")
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "To do")
        self.assertContains(response, "In progress")
        self.assertContains(response, "Done")
        self.assertContains(response, "Fix the login flow")

    def test_outsider_gets_404(self):
        self.client.force_login(self.outsider)
        response = self.client.get(f"/projects/{self.project.uuid}")
        self.assertEqual(response.status_code, 404)

    def test_board_partial(self):
        self.client.force_login(self.member)
        response = self.client.get(
            f"/projects/{self.project.uuid}",
            {"partial": "board"},
            HTTP_X_ALPINE_REQUEST="1",
        )
        self.assertTemplateUsed(response, "projects/ui/partials/board.html")

    def test_backlog_partial(self):
        self.client.force_login(self.member)
        response = self.client.get(
            f"/projects/{self.project.uuid}",
            {"partial": "backlog"},
            HTTP_X_ALPINE_REQUEST="1",
        )
        self.assertTemplateUsed(response, "projects/ui/partials/backlog.html")
