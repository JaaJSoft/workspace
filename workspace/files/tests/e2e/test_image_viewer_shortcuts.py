"""E2E cover for the image viewer's keyboard shortcuts: the keys its toolbar
tooltips name zoom, reset and turn the image. Skipped unless E2E=1 is set."""

from __future__ import annotations

import base64

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import FileShare
from workspace.files.services import tree

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
VIEWER = "[data-file-viewer-inline]"


class ImageViewerShortcutTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user(username="shortcut-owner")
        self.image = tree.create_file(
            owner=self.owner,
            name="photo.png",
            parent=None,
            content=SimpleUploadedFile("photo.png", PNG_1PX, content_type="image/png"),
            acting_user=self.owner,
        )

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _open(self, user, listing="/files?"):
        self.login_as(user)
        self.page.goto(f"{self.live_server_url}{listing}open={self.image.uuid}")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        viewer = self.page.locator(VIEWER)
        expect(viewer.locator("h3")).to_have_text("photo.png")
        return viewer

    def _zoom_label(self, viewer):
        return viewer.locator('[x-text*="zoomLevel"]')

    def _transform(self, viewer):
        return viewer.locator('[x-ref="image"]').evaluate(
            "(img) => img.parentElement.style.transform"
        )

    def test_plus_minus_and_zero_zoom_and_reset(self):
        viewer = self._open(self.owner)
        label = self._zoom_label(viewer)
        expect(label).to_have_text("100%")

        self.page.keyboard.press("+")
        expect(label).to_have_text("120%")
        self.page.keyboard.press("-")
        self.page.keyboard.press("-")
        expect(label).to_have_text("80%")
        self.page.keyboard.press("0")
        expect(label).to_have_text("100%")

    def test_r_turns_the_image_for_a_reader(self):
        reader = self.create_user(username="shortcut-reader")
        FileShare.objects.create(
            file=self.image,
            shared_by=self.owner,
            shared_with=reader,
            permission=FileShare.Permission.READ_ONLY,
        )
        viewer = self._open(reader, "/files?shared=1&")

        # The panel has mounted once its zoom label is drawn.
        expect(self._zoom_label(viewer)).to_have_text("100%")
        self.page.keyboard.press("r")
        self.page.wait_for_function(
            "() => document.querySelector('[data-file-viewer-inline] [x-ref=\"image\"]')"
            ".parentElement.style.transform.includes('rotate(90deg)')"
        )
        self.assertIn("rotate(90deg)", self._transform(viewer))
