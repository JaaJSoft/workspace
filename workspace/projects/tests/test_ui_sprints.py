from django.core.cache import cache
from django.test import TestCase

from workspace.projects.models import Project, Sprint
from workspace.projects.services.members import add_member
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin


class ScrumUiTestCase(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.scrum = create_project(
            self.admin, name="Rocket", project_type=Project.Type.SCRUM
        )
        add_member(self.scrum, self.member)
        self.todo = self.scrum.statuses.get(name="To do")
        self.board_url = f"/projects/{self.scrum.uuid}/board"

    def tearDown(self):
        cache.clear()
        super().tearDown()


class ScrumBoardUiTests(ScrumUiTestCase):
    def test_board_without_sprint_shows_empty_state(self):
        self.scrum.sprints.create(name="Sprint 1")
        self.client.force_login(self.member)
        response = self.client.get(self.board_url)
        self.assertContains(response, "No active sprint")
        self.assertContains(response, "Sprint 1")

    def test_start_button_is_admin_only(self):
        self.scrum.sprints.create(name="Sprint 1")
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.board_url), "startSprint(")
        self.client.force_login(self.member)
        self.assertNotContains(self.client.get(self.board_url), "startSprint(")

    def test_board_shows_active_sprint_tasks_only(self):
        sprint = self.scrum.sprints.create(name="Sprint 1", state=Sprint.State.ACTIVE)
        create_task(
            self.scrum, self.admin, title="In sprint", sprint=sprint, status=self.todo
        )
        other = self.scrum.sprints.create(name="Sprint 2")
        outside = create_task(
            self.scrum, self.admin, title="Other sprint", status=self.todo
        )
        outside.sprint = other
        outside.save(update_fields=["sprint"])
        self.client.force_login(self.member)
        response = self.client.get(self.board_url)
        self.assertContains(response, "In sprint")
        self.assertNotContains(response, "Other sprint")
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.board_url), "Complete sprint")

    def test_complete_button_is_admin_only(self):
        self.scrum.sprints.create(name="Sprint 1", state=Sprint.State.ACTIVE)
        self.client.force_login(self.member)
        self.assertNotContains(self.client.get(self.board_url), "Complete sprint")

    def test_closed_sprint_renders_read_only(self):
        active = self.scrum.sprints.create(name="Sprint 2", state=Sprint.State.ACTIVE)
        closed = self.scrum.sprints.create(name="Sprint 1", state=Sprint.State.CLOSED)
        create_task(
            self.scrum, self.admin, title="Old work", sprint=closed, status=self.todo
        )
        self.client.force_login(self.admin)
        response = self.client.get(f"{self.board_url}?sprint={closed.uuid}")
        self.assertContains(response, "Old work")
        # No completion, no card dragging, no per-column task creation on
        # a closed sprint.
        self.assertNotContains(response, "Complete sprint")
        self.assertNotContains(response, 'draggable="true"')
        self.assertNotContains(response, "newTask(")
        # The running sprint stays reachable from the switcher.
        self.assertContains(response, active.name)

    def test_kanban_board_has_no_sprint_bar(self):
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/board")
        self.assertNotContains(response, "board-sprint-data")
        self.assertNotContains(response, "No active sprint")


class ScrumBacklogUiTests(ScrumUiTestCase):
    def test_backlog_offers_send_to_sprint(self):
        self.scrum.sprints.create(name="Sprint 1")
        create_task(self.scrum, self.admin, title="Plan me")
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.scrum.uuid}/backlog")
        self.assertContains(response, "Send to sprint")
        self.assertContains(response, "sendSelectedToSprint(")
        # Sprint planning replaces the kanban send-to-board gesture, and
        # without a running sprint the per-row shortcut hides too.
        self.assertNotContains(response, "Send to board")
        self.assertNotContains(response, "Send to current sprint")

    def test_backlog_with_active_sprint_shows_row_shortcut(self):
        self.scrum.sprints.create(name="Sprint 1", state=Sprint.State.ACTIVE)
        create_task(self.scrum, self.admin, title="Plan me")
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.scrum.uuid}/backlog")
        self.assertContains(response, "Send to sprint")
        self.assertContains(response, "Send to current sprint")
        self.assertNotContains(response, "Send to board")

    def test_backlog_row_shows_sprint_chip(self):
        sprint = self.scrum.sprints.create(name="Sprint 1")
        create_task(self.scrum, self.admin, title="Planned", sprint=sprint)
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.scrum.uuid}/backlog")
        self.assertContains(response, 'name="Sprint 1"')

    def test_kanban_backlog_has_no_sprint_controls(self):
        create_task(self.project, self.admin, title="Plain")
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/backlog")
        self.assertNotContains(response, "Send to sprint")
        self.assertContains(response, "Send to board")


class ScrumSettingsUiTests(ScrumUiTestCase):
    def test_scrum_settings_show_sprint_section(self):
        self.client.force_login(self.admin)
        response = self.client.get(f"/projects/{self.scrum.uuid}/settings")
        self.assertContains(response, "projectSprints(")

    def test_kanban_settings_hide_sprint_section(self):
        self.client.force_login(self.admin)
        response = self.client.get(f"/projects/{self.project.uuid}/settings")
        self.assertNotContains(response, "projectSprints(")
