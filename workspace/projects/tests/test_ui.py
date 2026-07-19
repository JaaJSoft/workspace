from django.core.cache import cache
from django.test import TestCase

from workspace.projects.models import Project
from workspace.projects.services.tasks import create_task
from workspace.users.services.settings import get_setting, set_setting

from .base import ProjectTestMixin


class SettingsCleanupMixin:
    def tearDown(self):
        cache.clear()
        super().tearDown()


class IndexRedirectTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def test_no_setting_creates_personal_project_and_redirects(self):
        self.client.force_login(self.member)
        response = self.client.get("/projects")
        personal = Project.objects.get(
            created_by=self.member, type=Project.Type.PERSONAL
        )
        self.assertRedirects(
            response, f"/projects/{personal.uuid}", target_status_code=302
        )

    def test_redirects_to_last_opened_project(self):
        set_setting(self.member, "projects", "last_project", str(self.project.uuid))
        self.client.force_login(self.member)
        response = self.client.get("/projects")
        self.assertRedirects(
            response, f"/projects/{self.project.uuid}", target_status_code=302
        )

    def test_inaccessible_last_project_falls_back_to_personal(self):
        set_setting(self.outsider, "projects", "last_project", str(self.project.uuid))
        self.client.force_login(self.outsider)
        response = self.client.get("/projects")
        personal = Project.objects.get(
            created_by=self.outsider, type=Project.Type.PERSONAL
        )
        self.assertRedirects(
            response, f"/projects/{personal.uuid}", target_status_code=302
        )

    def test_malformed_last_project_falls_back_to_personal(self):
        set_setting(self.member, "projects", "last_project", "not-a-uuid")
        self.client.force_login(self.member)
        response = self.client.get("/projects")
        personal = Project.objects.get(
            created_by=self.member, type=Project.Type.PERSONAL
        )
        self.assertRedirects(
            response, f"/projects/{personal.uuid}", target_status_code=302
        )

    def test_anonymous_redirected_to_login(self):
        response = self.client.get("/projects")
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])


class ProjectRootRedirectTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def test_defaults_to_board(self):
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}")
        self.assertRedirects(response, f"/projects/{self.project.uuid}/board")

    def test_redirects_to_last_view(self):
        set_setting(
            self.member, "projects", f"last_view:{self.project.uuid}", "backlog"
        )
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}")
        self.assertRedirects(response, f"/projects/{self.project.uuid}/backlog")

    def test_unknown_last_view_falls_back_to_board(self):
        set_setting(self.member, "projects", f"last_view:{self.project.uuid}", "gantt")
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}")
        self.assertRedirects(response, f"/projects/{self.project.uuid}/board")


class BoardViewTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def test_renders_board_columns_without_backlog_column(self):
        todo_status = self.project.statuses.get(name="To do")
        create_task(
            self.project, self.admin, title="Fix the login flow", status=todo_status
        )
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/board")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "projects/ui/project.html")
        self.assertContains(response, "To do")
        self.assertContains(response, "In progress")
        self.assertContains(response, "Done")
        self.assertContains(response, "Fix the login flow")
        column_names = [c["status"].name for c in response.context["columns"]]
        self.assertNotIn("Backlog", column_names)
        self.assertEqual(response.context["view"], "board")

    def test_records_last_project_and_view(self):
        self.client.force_login(self.member)
        self.client.get(f"/projects/{self.project.uuid}/board")
        self.assertEqual(
            get_setting(self.member, "projects", "last_project"),
            str(self.project.uuid),
        )
        self.assertEqual(
            get_setting(self.member, "projects", f"last_view:{self.project.uuid}"),
            "board",
        )

    def test_partial_returns_content_wrapper_and_records_view(self):
        self.client.force_login(self.member)
        response = self.client.get(
            f"/projects/{self.project.uuid}/board", HTTP_X_ALPINE_REQUEST="1"
        )
        self.assertTemplateUsed(response, "projects/ui/partials/_content.html")
        self.assertTemplateNotUsed(response, "projects/ui/project.html")
        self.assertContains(response, 'id="project-content"')
        self.assertEqual(
            get_setting(self.member, "projects", f"last_view:{self.project.uuid}"),
            "board",
        )

    def test_members_data_exposes_user_ids(self):
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/board")
        self.assertIn(
            {"id": str(self.member.pk), "username": "member1"},
            response.context["members_data"],
        )

    def test_outsider_gets_404(self):
        self.client.force_login(self.outsider)
        response = self.client.get(f"/projects/{self.project.uuid}/board")
        self.assertEqual(response.status_code, 404)


class BacklogViewTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def test_renders_backlog_and_records_view(self):
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/backlog")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "projects/ui/project.html")
        self.assertEqual(response.context["view"], "backlog")
        self.assertEqual(
            get_setting(self.member, "projects", f"last_view:{self.project.uuid}"),
            "backlog",
        )

    def test_partial_returns_content_wrapper(self):
        self.client.force_login(self.member)
        response = self.client.get(
            f"/projects/{self.project.uuid}/backlog", HTTP_X_ALPINE_REQUEST="1"
        )
        self.assertTemplateUsed(response, "projects/ui/partials/_content.html")
        self.assertContains(response, 'id="project-content"')

    def test_outsider_gets_404(self):
        self.client.force_login(self.outsider)
        response = self.client.get(f"/projects/{self.project.uuid}/backlog")
        self.assertEqual(response.status_code, 404)
