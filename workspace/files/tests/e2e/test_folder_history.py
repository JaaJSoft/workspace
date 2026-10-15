"""E2E: moving between folders is browser history.

The listing swaps through alpine-ajax, and back or forward has to bring the
listing of the address it lands on, the first one loaded included (it
carried no history state, so nothing used to refresh it). The Back and
Forward buttons walk the same history as the browser's.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File


class FolderHistoryTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")
        self.folder = File.objects.create(
            owner=self.user, name="Reports", node_type=File.NodeType.FOLDER
        )
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/files")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        self.title = self.page.locator("#folder-browser h1")
        self.root_title = self.title.inner_text()

    def _open_folder(self):
        self.page.locator(f'a[data-folder-link][href$="{self.folder.uuid}"]').click()
        expect(self.title).to_have_text("Reports")

    def test_browser_back_to_the_first_folder_shows_its_listing(self):
        self._open_folder()

        self.page.go_back()

        expect(self.page).to_have_url(f"{self.live_server_url}/files")
        expect(self.title).to_have_text(self.root_title)

        self.page.go_forward()

        expect(self.title).to_have_text("Reports")

    def test_back_and_forward_buttons_walk_the_browser_history(self):
        back = self.page.get_by_title("Back (Alt+Left)")
        forward = self.page.get_by_title("Forward (Alt+Right)")
        expect(back).to_be_disabled()

        self._open_folder()
        expect(back).to_be_enabled()
        back.click()

        expect(self.page).to_have_url(f"{self.live_server_url}/files")
        expect(self.title).to_have_text(self.root_title)
        expect(forward).to_be_enabled()
        forward.click()
        expect(self.title).to_have_text("Reports")
        expect(forward).to_be_disabled()
