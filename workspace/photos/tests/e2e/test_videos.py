"""E2E cover for videos in the timeline: the tile, playback, and the fallback
when the browser cannot decode the file. Skipped unless E2E=1 is set."""

from __future__ import annotations

from datetime import UTC, datetime

from django.core.cache import cache
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.photos.tests.images import make_photo, make_video

TILES = "#timeline-grid [data-uuid]"


class PhotosVideoTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="filmmaker", is_staff=True)
        self.photo = make_photo(
            self.user, "beach.jpg", datetime(2024, 7, 14, 12, tzinfo=UTC)
        )
        self.video = make_video(
            self.user, "surf.webm", datetime(2024, 7, 14, 15, tzinfo=UTC)
        )
        self.login_as(self.user)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _open(self):
        self.page.goto(f"{self.live_server_url}/photos")
        self.page.wait_for_selector(TILES)

    def _tile(self, file_obj):
        return self.page.locator(f'{TILES}[data-uuid="{file_obj.uuid}"]')

    def test_a_video_tile_plays_in_the_viewer_among_the_photos(self):
        self._open()
        expect(self.page.locator("header")).to_contain_text("1 photo, 1 video")
        expect(self._tile(self.video).locator("[data-duration-badge]")).to_have_text(
            "0:03"
        )

        self._tile(self.video).click()

        dialog = self.page.locator("dialog.modal[open]")
        expect(dialog.locator("h3")).to_have_text("surf.webm")
        video = self.page.locator("#viewer-panel video")
        expect(video).to_be_visible()
        self.page.wait_for_function(
            "() => document.querySelector('#viewer-panel video').videoWidth > 0"
        )
        expect(self.page.locator("[data-video-unplayable]")).to_be_hidden()

        self.page.keyboard.press("ArrowRight")
        expect(dialog.locator("h3")).to_have_text("beach.jpg")

    def test_a_video_the_browser_cannot_decode_offers_a_download(self):
        # Chromium on Linux has no HEVC decoder: what an iPhone records.
        hevc = make_video(
            self.user,
            "iphone.mp4",
            datetime(2024, 7, 14, 18, tzinfo=UTC),
            clip="clip_hevc.mp4",
            video_codec="hevc",
        )
        self._open()

        self._tile(hevc).click()

        fallback = self.page.locator("[data-video-unplayable]")
        expect(fallback).to_be_visible()
        expect(fallback).to_contain_text("This browser can't play this video")
        expect(fallback).to_contain_text("It is encoded in HEVC")
        expect(fallback.get_by_role("link", name="Download")).to_have_attribute(
            "download", "iphone.mp4"
        )
        expect(self.page.locator("#viewer-panel video")).to_be_hidden()
