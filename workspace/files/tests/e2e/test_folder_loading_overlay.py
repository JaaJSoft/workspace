"""E2E test: navigating into a folder shows a loading overlay until the
new listing is bound.

The overlay lives outside ``#folder-browser`` (which alpine-ajax swaps),
appears on the ``ajax:send`` of a request that targets the listing, and
goes away on ``folder-browser-replaced``, the event the new listing
fires once Alpine has bound it. A request for anything else (properties
panel, activity feed) must leave the overlay alone.
"""

from __future__ import annotations

import time

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File


class FolderLoadingOverlayTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")
        self.folder = File.objects.create(
            owner=self.user, name="Reports", node_type=File.NodeType.FOLDER
        )
        File.objects.create(
            owner=self.user,
            parent=self.folder,
            name="q1.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )

    def _wait_for(self, held, timeout=5):
        deadline = time.monotonic() + timeout
        while not held and time.monotonic() < deadline:
            self.page.wait_for_timeout(50)
        self.assertTrue(held, "the request never went out")

    def _open_files(self):
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/files")
        # The debug toolbar overlays the pane and swallows pointer events.
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        return self.page.get_by_test_id("folder-loading")

    def test_overlay_covers_the_folder_request_then_clears(self):
        overlay = self._open_files()
        expect(overlay).to_be_hidden()

        # Hold the folder request so the loading state is observable.
        held = []
        self.page.route(
            f"**/files/{self.folder.uuid}*", lambda route: held.append(route)
        )

        self.page.locator(f'a[data-folder-link][href$="{self.folder.uuid}"]').click()
        self._wait_for(held)
        expect(overlay).to_be_visible()

        held[0].continue_()
        expect(self.page.locator("#folder-browser h1")).to_have_text("Reports")
        expect(overlay).to_be_hidden()

    def test_overlay_stays_invisible_for_the_first_200ms(self):
        # The fade-in is delayed so a folder that answers at once never
        # flashes a half-drawn veil. Playwright's visibility check ignores
        # opacity, so the computed style is what pins the delay down.
        overlay = self._open_files()

        held = []
        self.page.route(
            f"**/files/{self.folder.uuid}*", lambda route: held.append(route)
        )
        self.page.locator(f'a[data-folder-link][href$="{self.folder.uuid}"]').click()
        self._wait_for(held)
        expect(overlay).to_be_visible()
        self.assertEqual(overlay.evaluate("el => getComputedStyle(el).opacity"), "0")
        self.page.wait_for_timeout(600)
        self.assertEqual(overlay.evaluate("el => getComputedStyle(el).opacity"), "1")

        held[0].continue_()
        expect(overlay).to_be_hidden()

    def test_overlay_clears_when_a_refresh_fails(self):
        # A failed link navigation falls back to a full page load, which
        # takes the overlay away with the page. A failed in-place refresh
        # (the sync button) has no such fallback: the flag must clear.
        overlay = self._open_files()

        held = []

        def hold_the_swap(route):
            if route.request.headers.get("x-alpine-request"):
                held.append(route)
            else:
                route.continue_()

        self.page.route(f"{self.live_server_url}/files", hold_the_swap)
        self.page.locator('button[title="Sync & refresh"]').click()
        self._wait_for(held)
        expect(overlay).to_be_visible()

        held[0].fulfill(status=500, body="boom")
        expect(overlay).to_be_hidden()
        expect(self.page.locator("#folder-browser h1")).to_have_text("My Files")
        self.assertTrue(self.page.url.endswith("/files"))

    def test_an_unrelated_failure_keeps_the_overlay_up(self):
        # A properties request failing while the folder request is still
        # pending is not the folder request failing: the veil stays.
        overlay = self._open_files()

        held = []
        self.page.route(
            f"**/files/{self.folder.uuid}*", lambda route: held.append(route)
        )
        self.page.route(
            "**/files/properties/*",
            lambda route: route.fulfill(status=500, body="boom"),
        )
        self.page.locator(f'a[data-folder-link][href$="{self.folder.uuid}"]').click()
        self._wait_for(held)
        expect(overlay).to_be_visible()

        with self.page.expect_response(lambda r: "/files/properties/" in r.url):
            self.page.evaluate(
                "uuid => window.dispatchEvent(new CustomEvent('open-properties',"
                " { detail: { uuid, nodeType: 'folder' } }))",
                str(self.folder.uuid),
            )
        self.page.wait_for_timeout(300)
        expect(overlay).to_be_visible()

        held[0].continue_()
        expect(self.page.locator("#folder-browser h1")).to_have_text("Reports")
        expect(overlay).to_be_hidden()

    def test_a_properties_panel_request_does_not_show_the_overlay(self):
        overlay = self._open_files()

        held = []
        self.page.route("**/files/properties/*", lambda route: held.append(route))
        self.page.evaluate(
            "uuid => window.dispatchEvent(new CustomEvent('open-properties',"
            " { detail: { uuid, nodeType: 'folder' } }))",
            str(self.folder.uuid),
        )
        self._wait_for(held)
        expect(overlay).to_be_hidden()
        held[0].continue_()
