"""E2E cover for the viewer's slideshow on an album: the play button opens the
first photo and steps through the rest on its own, and the controls the
slideshow hides come back once it stops. The timing rules are pinned by
workspace/files/tests/js/viewer_slideshow.test.js; this proves the wiring in
a real browser (full screen, the merged panels, the gallery read off the
page). Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from datetime import UTC, datetime

from django.core.cache import cache
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.photos.services.albums import create_album
from workspace.photos.tests.images import make_photo

TILES = "#timeline-grid [data-uuid]"
VIEWER = "[data-file-viewer-inline]"
TOGGLE = "[data-viewer-slideshow-toggle]"


class AlbumSlideshowTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        # Photos is a preview module: staff see it.
        self.user = self.create_user(username="owner", is_staff=True)
        self.photos = [
            make_photo(
                self.user, f"day{day}.jpg", datetime(2024, 7, day, 12, tzinfo=UTC)
            )
            for day in (14, 13, 12)
        ]
        self.album = create_album(self.user, "Summer", files=self.photos)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _open_album(self):
        self.login_as(self.user)
        # Installed before the page loads, so the slideshow's timers run on it.
        self.page.clock.install()
        self.page.goto(f"{self.live_server_url}/photos/albums/{self.album.uuid}")
        self.page.wait_for_selector(TILES)

    def _shown(self, name):
        viewer = self.page.locator(VIEWER)
        expect(viewer.locator("h3")).to_have_text(name)
        expect(viewer.locator("#viewer-panel img")).to_be_visible()
        self.page.wait_for_function(
            "() => document.querySelector('#viewer-panel img')?.complete"
        )

    def test_the_album_play_button_steps_through_its_photos(self):
        self._open_album()

        self.page.locator("[data-album-slideshow]").click()

        self._shown("day14.jpg")
        toggle = self.page.locator(TOGGLE)
        expect(toggle).to_have_attribute("aria-pressed", "true")
        self.page.wait_for_function("() => !!document.fullscreenElement")
        # Zoom and edit make no sense in a slideshow.
        expect(self.page.locator("#viewer-panel [data-viewer-toolbar]")).to_be_hidden()

        self.page.clock.run_for(5000)
        self._shown("day13.jpg")
        self.page.clock.run_for(5000)
        self._shown("day12.jpg")

        # The last photo waits a turn for a next page, then the slideshow ends
        # on it, its controls back.
        self.page.clock.run_for(10000)
        expect(toggle).to_have_attribute("aria-pressed", "false")
        expect(self.page.locator(VIEWER).locator("h3")).to_have_text("day12.jpg")
        expect(self.page.locator("#viewer-panel [data-viewer-toolbar]")).to_be_visible()
        self.page.wait_for_function("() => !document.fullscreenElement")

    def test_space_pauses_the_slideshow_and_escape_keeps_the_viewer_open(self):
        self._open_album()
        self.page.locator(TILES).nth(1).locator("[data-viewer-focus]").click()
        self._shown("day13.jpg")

        self.page.locator(VIEWER).focus()
        self.page.keyboard.press("Space")
        toggle = self.page.locator(TOGGLE)
        expect(toggle).to_have_attribute("aria-pressed", "true")

        self.page.keyboard.press("Space")
        expect(toggle).to_have_attribute("aria-pressed", "false")
        self.page.clock.run_for(10000)
        expect(self.page.locator(VIEWER).locator("h3")).to_have_text("day13.jpg")

        self.page.keyboard.press("Space")
        expect(toggle).to_have_attribute("aria-pressed", "true")
        self.page.keyboard.press("Escape")
        expect(toggle).to_have_attribute("aria-pressed", "false")
        expect(self.page.locator(VIEWER)).to_be_visible()
