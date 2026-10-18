"""E2E cover for the Files viewer shown in place of the listing.

What only a rendered page can prove: the viewer takes the listing's place
and not the properties panel's, the listing's shortcuts stay off the rows
behind it, the properties button follows the gallery, and a sidebar link
takes the viewer away. Skipped unless E2E=1 is set.
"""

from __future__ import annotations

import base64
import re

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import FileFavorite
from workspace.files.services import FileService

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
VIEWER = "[data-file-viewer-inline]"
ACTIVE = re.compile(r"(^|\s)btn-active(\s|$)")


class InlineViewerTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="inline-viewer")
        self.images = [
            FileService.create_file(
                owner=self.user,
                name=name,
                content=SimpleUploadedFile(name, PNG_1PX, content_type="image/png"),
                acting_user=self.user,
            )
            for name in ("alpha.png", "bravo.png")
        ]

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _row(self, file_obj):
        return self.page.locator(f'#folder-browser [data-uuid="{file_obj.uuid}"]')

    def _open_listing(self, path="/files"):
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}{path}")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        self.page.wait_for_function(
            """(uuids) => {
              const root = document.querySelector('[x-data*="fileTableWithView"]');
              const data = root && Alpine.$data(root);
              return !!data && uuids.every((u) => (data.actionsMap[u] || []).length > 0);
            }""",
            arg=[str(f.uuid) for f in self.images],
        )

    def _open(self, file_obj):
        self._row(file_obj).locator('td[data-col="size"]').click()
        viewer = self.page.locator(VIEWER)
        expect(viewer.locator("h3")).to_have_text(file_obj.name)
        return viewer

    def test_a_file_opens_in_place_of_the_listing(self):
        self._open_listing()

        viewer = self._open(self.images[0])

        expect(self.page.locator("dialog.modal[open]")).to_have_count(0)
        self.assertEqual(
            viewer.bounding_box(), self.page.locator("#files-main").bounding_box()
        )
        self.assertTrue(
            self.page.evaluate("document.getElementById('files-main').inert")
        )
        self.assertIn(f"open={self.images[0].uuid}", self.page.url)

        self.page.keyboard.press("ArrowRight")
        expect(viewer.locator("h3")).to_have_text("bravo.png")

        viewer.get_by_role("button", name="Back").click()
        expect(viewer).to_be_hidden()
        self.assertFalse(
            self.page.evaluate("document.getElementById('files-main').inert")
        )
        self.assertNotIn("open=", self.page.url)

    def test_listing_shortcuts_stay_off_the_rows_behind_the_viewer(self):
        self._open_listing()
        self._row(self.images[0]).locator("input[data-select-item]").check()

        self.page.evaluate(
            """(f) => window.dispatchEvent(new CustomEvent('open-file-viewer', { detail: f }))""",
            {"uuid": str(self.images[1].uuid), "name": "bravo.png", "type": "image"},
        )
        expect(self.page.locator(f"{VIEWER} h3")).to_have_text("bravo.png")
        self.page.keyboard.press("F2")
        self.page.wait_for_timeout(200)

        expect(self.page.locator("#rename-dialog")).not_to_have_attribute("open", "")

    def test_the_properties_button_opens_the_panel_and_follows_the_gallery(self):
        self._open_listing()
        viewer = self._open(self.images[0])
        button = viewer.get_by_role("button", name="Properties")
        panel = self.page.locator("#properties-sidebar")

        expect(button).to_be_enabled()
        button.click()

        expect(panel).to_be_visible()
        expect(panel.locator("#properties-content h3")).to_have_text("alpha.png")
        expect(button).to_have_class(ACTIVE)
        # Beside the viewer, not under it, once the panel has slid in.
        self.page.wait_for_function(
            "() => document.getElementById('properties-sidebar').getAnimations().length === 0"
        )
        viewer_box = viewer.bounding_box()
        panel_box = panel.bounding_box()
        self.assertLessEqual(viewer_box["x"] + viewer_box["width"], panel_box["x"] + 1)

        self.page.keyboard.press("ArrowRight")
        expect(viewer.locator("h3")).to_have_text("bravo.png")
        expect(panel.locator("#properties-content h3")).to_have_text("bravo.png")

        self.page.keyboard.press("Control+i")
        expect(panel).to_be_hidden()
        expect(button).not_to_have_class(ACTIVE)

    def test_a_sidebar_link_takes_the_viewer_away(self):
        FileFavorite.objects.create(owner=self.user, file=self.images[1])
        self._open_listing()
        viewer = self._open(self.images[0])

        self.page.locator(".drawer-side a[href='/files?favorites=1']").click()

        expect(self.page).to_have_url(f"{self.live_server_url}/files?favorites=1")
        expect(viewer).to_be_hidden()
        expect(self.page.locator("#folder-browser [data-uuid]")).to_have_count(1)
        self.page.go_back()
        expect(viewer.locator("h3")).to_have_text("alpha.png")
