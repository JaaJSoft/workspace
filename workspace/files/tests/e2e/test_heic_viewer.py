"""E2E: a HEIC photo opens in the image viewer of a browser that cannot decode it.

Chromium shows no HEIC, so the <img> errors and the viewer converts the bytes
with the vendored decoder. If the decoder fails to load, or the global it
declares changes name, the viewer is left on a broken image.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from django.core.files.base import ContentFile

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.services import FileService
from workspace.files.tests.rasters import heic_bytes


class HeicViewerTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="heic")
        self.file = FileService.create_file(
            owner=self.user,
            name="IMG_0001.HEIC",
            content=ContentFile(heic_bytes(size=(96, 64)), name="IMG_0001.HEIC"),
        )

    def test_a_heic_is_converted_and_shown(self):
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/files?open={self.file.uuid}")

        image = self.page.locator("#viewer-panel img[x-ref=image]")
        image.wait_for(state="attached")
        self.page.wait_for_function(
            """() => {
                const img = document.querySelector('#viewer-panel img[x-ref=image]');
                return img && img.src.startsWith('blob:') && img.complete
                    && img.naturalWidth > 0;
            }""",
            timeout=30_000,
        )
        self.assertEqual(
            image.evaluate("img => [img.naturalWidth, img.naturalHeight]"), [96, 64]
        )
