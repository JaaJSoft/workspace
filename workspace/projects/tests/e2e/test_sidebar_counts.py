"""E2E test: the sidebar badges follow the tasks moved from the content pane.

The sidebar (the Backlog badge, the current project's open count in the
switcher) is rendered outside ``#project-content``, the element every action
swaps. The counts ride along in each swapped fragment and the shell copies
them over, so a task leaving the backlog must decrement the badge without a
page reload.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task


class SidebarCountsTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")
        self.project = create_project(self.user, name="Website")
        # A new task lands in the backlog column.
        create_task(self.project, self.user, title="Write the brief")
        create_task(self.project, self.user, title="Plan the launch")

    def _badge(self):
        return self.page.locator(r'a[x-target\.push][href$="/backlog"] .badge')

    def test_sending_a_task_to_the_board_decrements_the_backlog_badge(self):
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/projects/{self.project.uuid}/backlog")
        # The debug toolbar overlays the pane and swallows pointer events.
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        expect(self._badge()).to_have_text("2")

        rows = self.page.locator("#project-content [data-task-uuid]")
        expect(rows).to_have_count(2)
        rows.first.get_by_title("Send to board").click()

        expect(rows).to_have_count(1)
        expect(self._badge()).to_have_text("1")

    def test_badge_hides_once_the_backlog_is_empty(self):
        self.project.tasks.filter(title="Plan the launch").delete()
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/projects/{self.project.uuid}/backlog")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        expect(self._badge()).to_have_text("1")

        self.page.get_by_title("Send to board").click()

        expect(self.page.locator("#project-content [data-task-uuid]")).to_have_count(0)
        expect(self._badge()).to_be_hidden()
