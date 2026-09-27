"""E2E test: switching the library or the media type shows a loading veil
until the new listing is swapped in.

The veil lives outside ``#photos-content`` (which alpine-ajax swaps), shows
on the ``ajax:send`` of a request that targets it, and goes away on
``ajax:merged``. A request that only refreshes the sidebar leaves it alone.
Skipped unless E2E=1 is set.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from django.contrib.auth.models import Group
from django.core.cache import cache
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.services import FileService
from workspace.photos.tests.images import make_photo, make_video

TILES = "#timeline-grid [data-uuid]"


class PhotosLoadingOverlayTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="photographer", is_staff=True)
        make_photo(self.user, "mine.jpg", datetime(2024, 7, 10, 12, tzinfo=UTC))
        # A photo and a video, or the media type tabs are not offered.
        make_video(self.user, "surf.webm", datetime(2024, 7, 10, 15, tzinfo=UTC))
        self.family = Group.objects.create(name="Family")
        self.user.groups.add(self.family)
        bob = self.create_user(username="bob")
        root = FileService.create_folder(owner=bob, name="Family", group=self.family)
        make_photo(bob, "family.jpg", datetime(2024, 7, 9, 12, tzinfo=UTC), parent=root)
        self.login_as(self.user)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _open(self):
        self.page.goto(f"{self.live_server_url}/photos")
        self.page.wait_for_selector(TILES)
        # The debug toolbar overlays the page and swallows pointer events.
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        return self.page.get_by_test_id("photos-loading")

    def _hold(self, pattern):
        held = []

        def hold_the_swap(route):
            if route.request.headers.get("x-alpine-request"):
                held.append(route)
            else:
                route.continue_()

        self.page.route(pattern, hold_the_swap)
        return held

    def _wait_for(self, held, timeout=5):
        deadline = time.monotonic() + timeout
        while not held and time.monotonic() < deadline:
            self.page.wait_for_timeout(50)
        self.assertTrue(held, "the request never went out")

    def test_a_scope_tab_shows_the_veil_until_the_listing_is_swapped(self):
        overlay = self._open()
        expect(overlay).to_be_hidden()
        held = self._hold("**/photos?scope=*")

        self.page.locator("nav[aria-label='Library'] a", has_text="Family").click()
        self._wait_for(held)
        expect(overlay).to_be_visible()
        # The fade-in is delayed, so a quick answer never flashes the veil.
        self.assertEqual(overlay.evaluate("el => getComputedStyle(el).opacity"), "0")
        self.page.wait_for_timeout(600)
        self.assertEqual(overlay.evaluate("el => getComputedStyle(el).opacity"), "1")

        held[0].continue_()
        expect(self.page).to_have_url(
            f"{self.live_server_url}/photos?scope=group%3A{self.family.pk}"
        )
        expect(self.page.locator(TILES)).to_have_count(1)
        expect(overlay).to_be_hidden()

    def test_a_media_type_tab_shows_the_veil(self):
        overlay = self._open()
        held = self._hold("**/photos?type=*")

        self.page.locator("nav[aria-label='Media type'] a[title='Videos only']").click()
        self._wait_for(held)
        expect(overlay).to_be_visible()

        held[0].continue_()
        expect(self.page.locator(TILES)).to_have_count(1)
        expect(overlay).to_be_hidden()

    def test_a_failed_swap_clears_the_veil(self):
        overlay = self._open()
        held = self._hold("**/photos?scope=*")

        self.page.locator("nav[aria-label='Library'] a", has_text="Family").click()
        self._wait_for(held)
        expect(overlay).to_be_visible()

        held[0].fulfill(status=500, body="boom")
        expect(overlay).to_be_hidden()

    def test_a_swap_that_gets_no_answer_clears_the_veil(self):
        # A network failure rejects the request without any ajax event.
        overlay = self._open()
        held = self._hold("**/photos?scope=*")

        self.page.locator("nav[aria-label='Library'] a", has_text="Family").click()
        self._wait_for(held)
        expect(overlay).to_be_visible()

        held[0].abort()
        expect(overlay).to_be_hidden()

    def test_a_refresh_after_a_lost_swap_does_not_show_the_veil(self):
        # The lost request leaves #photos-content marked aria-busy: only the
        # targets of the request being sent may raise the veil.
        overlay = self._open()
        lost = self._hold("**/photos?scope=*")
        self.page.locator("nav[aria-label='Library'] a", has_text="Family").click()
        self._wait_for(lost)
        lost[0].abort()
        expect(overlay).to_be_hidden()
        expect(self.page.locator("#photos-content")).to_have_attribute(
            "aria-busy", "true"
        )

        held = self._hold(f"{self.live_server_url}/photos")
        self.page.evaluate("window.dispatchEvent(new CustomEvent('tags-changed'))")
        self._wait_for(held)
        self.page.wait_for_timeout(300)
        expect(overlay).to_be_hidden()
        held[0].continue_()

    def test_a_sidebar_refresh_does_not_show_the_veil(self):
        overlay = self._open()
        held = self._hold(f"{self.live_server_url}/photos")

        self.page.evaluate("window.dispatchEvent(new CustomEvent('tags-changed'))")
        self._wait_for(held)
        self.page.wait_for_timeout(300)
        expect(overlay).to_be_hidden()
        held[0].continue_()
