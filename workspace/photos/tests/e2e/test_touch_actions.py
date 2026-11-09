"""A photo's menu button on a touch screen.

A phone never hovers, and a button on every tile of the grid would take the
taps that open the photos. The button shows on a selected tile only: a long
press selects one, and its button then opens the photo's own menu. Run in a
context where ``(hover: none)`` matches.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from datetime import UTC, datetime

from django.core.cache import cache
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import TOUCH_CONTEXT, PlaywrightTestCase
from workspace.photos.tests.images import make_photo

TILES = "#timeline-grid [data-uuid]"


class PhotosTouchActionsTests(PlaywrightTestCase):
    CONTEXT_OPTIONS = TOUCH_CONTEXT

    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="touch-photos")
        for day in (14, 13):
            make_photo(
                self.user, f"day{day}.jpg", datetime(2024, 7, day, 12, tzinfo=UTC)
            )
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/photos")
        self.page.wait_for_selector(TILES)
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _long_press(self, locator):
        box = locator.bounding_box()
        point = {"x": box["x"] + box["width"] / 2, "y": box["y"] + box["height"] / 2}
        cdp = self.context.new_cdp_session(self.page)
        cdp.send(
            "Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [point]}
        )
        self.page.wait_for_timeout(700)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})

    def test_the_menu_button_waits_for_a_long_press(self):
        tile = self.page.locator(TILES).first
        other = self.page.locator(TILES).nth(1)
        more = tile.get_by_role("button", name="More actions for day14.jpg")
        expect(more).to_be_hidden()

        self._long_press(tile)

        expect(tile).to_have_attribute("data-selected", "1")
        expect(more).to_be_visible()
        expect(more).to_have_css("opacity", "1")
        expect(
            other.get_by_role("button", name="More actions for day13.jpg")
        ).to_be_hidden()

        more.tap()
        expect(self.page.locator("#photos-context-menu")).to_be_visible()
