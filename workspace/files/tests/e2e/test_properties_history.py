"""E2E: the properties panel is a history entry.

On a phone the panel covers the listing, and the browser's back is the way
out the user reaches for: it must close the panel rather than leave the
page. Only a real browser history can prove it. Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File


class PropertiesHistoryTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")
        self.folder = File.objects.create(
            owner=self.user, name="Reports", node_type=File.NodeType.FOLDER
        )
        self.file = File.objects.create(
            owner=self.user, name="hello.txt", node_type=File.NodeType.FILE
        )
        self.login_as(self.user)
        self.page.set_viewport_size({"width": 375, "height": 800})
        self.page.goto(f"{self.live_server_url}/files")
        self.panel = self.page.locator("#properties-sidebar")

    def _open_properties(self, node):
        self.page.evaluate(
            "([uuid, nodeType]) => window.dispatchEvent(new CustomEvent("
            "'open-properties', { detail: { uuid, nodeType } }))",
            [str(node.uuid), node.node_type],
        )
        expect(self.panel.locator("#properties-content")).to_contain_text(node.name)

    def test_browser_back_closes_the_panel_and_forward_reopens_it(self):
        self._open_properties(self.file)
        self.assertIn(f"properties={self.file.uuid}", self.page.url)

        self.page.go_back()

        expect(self.panel).to_be_hidden()
        expect(self.page).to_have_url(f"{self.live_server_url}/files")

        self.page.go_forward()
        expect(self.panel.locator("#properties-content")).to_contain_text("hello.txt")

    def test_back_from_another_folder_brings_the_panel_back(self):
        self._open_properties(self.folder)

        self.page.set_viewport_size({"width": 1280, "height": 900})
        self.page.locator(
            f'#folder-browser a[data-folder-link][href$="{self.folder.uuid}"]'
        ).click()
        expect(self.page.locator("#folder-browser h1")).to_have_text("Reports")
        expect(self.panel).to_be_hidden()

        self.page.go_back()

        expect(self.panel.locator("#properties-content")).to_contain_text("Reports")
        self.assertIn(f"properties={self.folder.uuid}", self.page.url)

        self.page.go_back()
        expect(self.panel).to_be_hidden()
