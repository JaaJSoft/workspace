"""E2E: a stream reconnection never discards what the user is typing.

Each reconnection re-renders the board content and the open task panel from
the server, which rebuilds every component inside them. A region holding
text being typed is left alone, and catches up once that text is saved,
sent or abandoned. The other region re-syncs as usual and serves as the
point where the reconnection has been handled.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.projects.models import Task
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task

RECONNECT = "window.dispatchEvent(new CustomEvent('sse:reconnect'))"


class ReconnectionKeepsDraftsTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")
        self.project = create_project(self.user, name="Website")
        self.task = create_task(self.project, self.user, title="Write the brief")
        self.base = f"{self.live_server_url}/projects/{self.project.uuid}"
        self.login_as(self.user)
        self.requests: list[str] = []
        self.page.on("request", lambda request: self.requests.append(request.url))

    def _open(self, path):
        self.page.goto(f"{self.base}{path}?task={self.task.uuid}")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        self.panel = self.page.locator("#task-panel")
        expect(self.panel).to_contain_text("Write the brief")

    def _rename_elsewhere(self):
        Task.objects.filter(uuid=self.task.uuid).update(title="Renamed elsewhere")

    def _reconnect(self, handled):
        """Dispatch a reconnection, then wait on the region that re-syncs."""
        self.requests.clear()
        self.page.evaluate(RECONNECT)
        expect(handled).to_contain_text("Renamed elsewhere")

    def _panel_requests(self):
        return [url for url in self.requests if url.endswith("/panel")]

    def test_a_description_being_typed_survives_and_the_panel_catches_up_on_cancel(
        self,
    ):
        self._open("/backlog")
        self.panel.get_by_role("button", name="Description").click()
        self.panel.get_by_text("No description.").click()
        editor = self.panel.get_by_placeholder("Markdown supported")
        editor.fill("Half-written description")
        self._rename_elsewhere()

        self._reconnect(self.page.locator("#project-content"))

        self.assertEqual(self._panel_requests(), [])
        expect(editor).to_have_value("Half-written description")
        expect(self.panel).not_to_contain_text("Renamed elsewhere")

        self.panel.get_by_role("button", name="Cancel").click()

        expect(self.panel).to_contain_text("Renamed elsewhere")

    def test_a_comment_being_typed_survives_and_the_panel_catches_up_once_emptied(self):
        self._open("/backlog")
        self.panel.get_by_role("button", name="Comments").click()
        composer = self.panel.get_by_placeholder("Add a comment...")
        composer.fill("A comment in progress")
        self._rename_elsewhere()

        self._reconnect(self.page.locator("#project-content"))

        self.assertEqual(self._panel_requests(), [])
        expect(composer).to_have_value("A comment in progress")

        composer.fill("")

        expect(self.panel).to_contain_text("Renamed elsewhere")

    def test_a_panel_without_a_draft_re_syncs(self):
        self._open("/backlog")
        self._rename_elsewhere()

        self._reconnect(self.panel)

        expect(self.page.locator("#project-content")).to_contain_text(
            "Renamed elsewhere"
        )

    def test_a_settings_field_being_typed_survives(self):
        self._open("/settings")
        name = self.page.locator("#project-content input[x-model='name']")
        name.fill("Half-typed name")
        self._rename_elsewhere()

        self._reconnect(self.panel)

        self.assertEqual([url for url in self.requests if "/settings" in url], [])
        expect(name).to_have_value("Half-typed name")
