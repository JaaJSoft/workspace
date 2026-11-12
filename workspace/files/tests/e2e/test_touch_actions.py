"""The listing's hover-revealed controls on a touch screen.

A phone never hovers. The "more actions" button and the mosaic card's
checkbox stay on screen there; the empty favorite star of a mosaic card does
not, since the card's menu carries the same toggle. Run in a context where
``(hover: none)`` matches.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import TOUCH_CONTEXT, PlaywrightTestCase
from workspace.files.models import File
from workspace.users.services.settings import set_setting

MENU = '[x-data*="contextMenu"]'


class FilesTouchActionsTests(PlaywrightTestCase):
    CONTEXT_OPTIONS = TOUCH_CONTEXT

    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="touch-files")
        self.file = File.objects.create(
            owner=self.user,
            name="notes.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )

    def _open_files(self, view_mode):
        set_setting(self.user, "files", "preferences", {"defaultViewMode": view_mode})
        self.login_as(self.user)
        with self.page.expect_response(
            lambda r: r.request.method == "POST" and "/api/v1/files/actions" in r.url
        ):
            self.page.goto(f"{self.live_server_url}/files")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        return self.page.locator(f'#folder-browser [data-uuid="{self.file.uuid}"]')

    def _assert_menu_opens_with_favorite(self, item):
        more = item.locator('[data-item-control="menu"]')
        expect(more).to_have_css("opacity", "1")
        more.tap()
        menu = self.page.locator(MENU)
        expect(menu).to_be_visible()
        expect(menu.get_by_role("button", name="Add to favorites")).to_be_visible()

    def test_list_row_shows_its_menu_button(self):
        row = self._open_files("list")
        self._assert_menu_opens_with_favorite(row)

    def test_mosaic_card_shows_its_menu_button_and_checkbox(self):
        card = self._open_files("mosaic")
        expect(card.locator("[data-select-item]")).to_have_css("opacity", "1")
        expect(
            card.locator('[data-item-control="favorite"]')
        ).to_be_hidden()  # in the menu instead
        self._assert_menu_opens_with_favorite(card)

    def test_mosaic_checkbox_selects_on_tap(self):
        card = self._open_files("mosaic")
        card.locator("[data-select-item]").tap()
        expect(card).to_have_attribute("data-selected", "")
