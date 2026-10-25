"""E2E: a camera RAW opens in the image viewer, as the JPEG preview it embeds.

No browser decodes a RAW: the viewer loads the vendored LibRaw build and shows
the preview it extracts, turned by the RAW's orientation. If the decoder fails
to load, or the orientation is lost, the photo shows blank or on its side.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from django.core.files.base import ContentFile

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.services import FileService
from workspace.files.tests.rasters import dng_bytes


class RawViewerTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="raw")
        data = dng_bytes(orientation=6, preview_size=(96, 64))
        self.file = FileService.create_file(
            owner=self.user,
            name="IMG_0001.DNG",
            content=ContentFile(data, name="IMG_0001.DNG"),
        )

    def test_a_raw_shows_its_preview_the_right_way_up(self):
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
            image.evaluate("img => [img.naturalWidth, img.naturalHeight]"), [64, 96]
        )
