"""E2E: a project's views and its task panel are browser history.

Back from a view swapped in through the sidebar has to bring the previous
view back, the first one loaded included. Opening a task adds an entry, so
back closes the panel, and closing it steps back instead of leaving a
duplicate entry behind.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task


class ProjectHistoryTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")
        self.project = create_project(self.user, name="Website")
        self.task = create_task(self.project, self.user, title="Write the brief")
        self.base = f"{self.live_server_url}/projects/{self.project.uuid}"
        self.login_as(self.user)

    def _open(self, path):
        self.page.goto(f"{self.base}{path}")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")

    def test_back_to_the_first_view_loaded_shows_it(self):
        self._open("/backlog")
        rows = self.page.locator("#project-content [data-task-uuid]")
        expect(rows).to_have_count(1)

        self.page.locator(r'a[x-target\.push][href$="/board"]').first.click()
        expect(self.page).to_have_url(f"{self.base}/board")
        expect(rows).to_have_count(0)
        self.page.go_back()

        expect(self.page).to_have_url(f"{self.base}/backlog")
        expect(rows).to_have_count(1)

    def test_back_closes_the_task_panel_and_closing_steps_back(self):
        self._open("/backlog")
        panel = self.page.locator("#task-panel")
        close = self.page.get_by_role("button", name="Close panel")
        row = self.page.locator(f"#project-content [data-task-uuid='{self.task.uuid}']")

        row.click()
        expect(panel).to_contain_text("Write the brief")
        self.page.go_back()

        expect(self.page).to_have_url(f"{self.base}/backlog")
        expect(close).to_be_hidden()

        row.click()
        expect(panel).to_contain_text("Write the brief")
        close.click()

        expect(self.page).to_have_url(f"{self.base}/backlog")
        self.page.go_forward()
        expect(self.page).to_have_url(f"{self.base}/backlog?task={self.task.uuid}")
