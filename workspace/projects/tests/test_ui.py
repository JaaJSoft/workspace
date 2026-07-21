from django.core.cache import cache
from django.test import TestCase

from workspace.projects.models import Project
from workspace.projects.services.projects import get_or_create_personal_project
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
        self.assertRedirects(response, f"/projects/{personal.uuid}")

    def test_redirects_to_last_opened_project(self):
        set_setting(self.member, "projects", "last_project", str(self.project.uuid))
        self.client.force_login(self.member)
        response = self.client.get("/projects")
        self.assertRedirects(response, f"/projects/{self.project.uuid}")

    def test_inaccessible_last_project_falls_back_to_personal(self):
        set_setting(self.outsider, "projects", "last_project", str(self.project.uuid))
        self.client.force_login(self.outsider)
        response = self.client.get("/projects")
        personal = Project.objects.get(
            created_by=self.outsider, type=Project.Type.PERSONAL
        )
        self.assertRedirects(response, f"/projects/{personal.uuid}")

    def test_malformed_last_project_falls_back_to_personal(self):
        set_setting(self.member, "projects", "last_project", "not-a-uuid")
        self.client.force_login(self.member)
        response = self.client.get("/projects")
        personal = Project.objects.get(
            created_by=self.member, type=Project.Type.PERSONAL
        )
        self.assertRedirects(response, f"/projects/{personal.uuid}")

    def test_anonymous_redirected_to_login(self):
        response = self.client.get("/projects")
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])


class OverviewViewTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def test_renders_overview_with_task_counts(self):
        todo_status = self.project.statuses.get(name="To do")
        backlog_status = self.project.statuses.get(name="Backlog")
        done_status = self.project.statuses.get(name="Done")
        create_task(self.project, self.admin, title="Active", status=todo_status)
        create_task(self.project, self.admin, title="Queued", status=backlog_status)
        create_task(self.project, self.admin, title="Shipped", status=done_status)
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "projects/ui/project.html")
        self.assertEqual(response.context["view"], "overview")
        self.assertEqual(response.context["board_count"], 1)
        self.assertEqual(response.context["backlog_count"], 1)
        self.assertEqual(response.context["done_count"], 1)
        self.assertContains(response, "Members")
        self.assertContains(response, "member1")
        self.assertContains(response, "admin1")

    def test_records_last_project(self):
        self.client.force_login(self.member)
        self.client.get(f"/projects/{self.project.uuid}")
        self.assertEqual(
            get_setting(self.member, "projects", "last_project"),
            str(self.project.uuid),
        )

    def test_partial_returns_content_wrapper(self):
        self.client.force_login(self.member)
        response = self.client.get(
            f"/projects/{self.project.uuid}", HTTP_X_ALPINE_REQUEST="1"
        )
        self.assertTemplateUsed(response, "projects/ui/partials/_content.html")
        self.assertTemplateNotUsed(response, "projects/ui/project.html")
        self.assertContains(response, 'id="project-content"')
        self.assertContains(response, 'id="overview"')

    def test_outsider_gets_404(self):
        self.client.force_login(self.outsider)
        response = self.client.get(f"/projects/{self.project.uuid}")
        self.assertEqual(response.status_code, 404)


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

    def test_records_last_project(self):
        self.client.force_login(self.member)
        self.client.get(f"/projects/{self.project.uuid}/board")
        self.assertEqual(
            get_setting(self.member, "projects", "last_project"),
            str(self.project.uuid),
        )

    def test_partial_returns_content_wrapper(self):
        self.client.force_login(self.member)
        response = self.client.get(
            f"/projects/{self.project.uuid}/board", HTTP_X_ALPINE_REQUEST="1"
        )
        self.assertTemplateUsed(response, "projects/ui/partials/_content.html")
        self.assertTemplateNotUsed(response, "projects/ui/project.html")
        self.assertContains(response, 'id="project-content"')

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
    def test_renders_backlog(self):
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/backlog")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "projects/ui/project.html")
        self.assertEqual(response.context["view"], "backlog")

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


class OverviewActivityTests(ProjectTestMixin, TestCase):
    def test_overview_shows_recent_events(self):
        create_task(self.project, self.admin, title="Paint the shed")
        self.client.force_login(self.admin)
        resp = self.client.get(f"/projects/{self.project.uuid}")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Recent activity")
        self.assertContains(resp, "Task created")
        self.assertContains(resp, "Paint the shed")

    def test_overview_empty_activity_state(self):
        self.client.force_login(self.admin)
        resp = self.client.get(f"/projects/{self.project.uuid}")
        self.assertContains(resp, "No activity yet.")


class SidebarTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def test_switcher_lists_only_accessible_projects(self):
        personal = get_or_create_personal_project(self.member)
        other = Project.objects.create(
            name="Admin only", created_by=self.admin, type=Project.Type.KANBAN
        )
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/board")
        self.assertContains(response, "Website")
        self.assertContains(response, personal.name)
        self.assertNotContains(response, "Admin only")
        sidebar_uuids = [p.uuid for p in response.context["projects"]]
        self.assertNotIn(other.uuid, sidebar_uuids)
        self.assertEqual(sidebar_uuids[0], personal.uuid)

    def test_sidebar_links_to_project_views(self):
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/board")
        self.assertContains(response, f"/projects/{self.project.uuid}/backlog")
        self.assertContains(response, f"/projects/{self.project.uuid}/board")

    def test_partial_response_has_no_sidebar(self):
        self.client.force_login(self.member)
        response = self.client.get(
            f"/projects/{self.project.uuid}/board", HTTP_X_ALPINE_REQUEST="1"
        )
        self.assertNotContains(response, "drawer-side")
        self.assertNotIn("projects", response.context)
