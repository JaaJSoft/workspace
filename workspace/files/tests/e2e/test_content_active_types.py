"""E2E: a stored HTML or SVG file never runs as a page of the app.

A file's content URL is served from the app's origin. Opened directly (the
viewer's "New tab" link, a URL someone sent), an HTML or SVG file would run its
scripts with the viewer's session. What only a browser can prove: no script of
the file reaches the app's origin, a plain image still opens in the tab, the
PDF viewer's iframe still shows the content URL, and the image viewer still
draws an SVG. Skipped unless E2E=1 is set.
"""

from __future__ import annotations

import base64
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.services import FileService

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
MARK = "localStorage.setItem('ran-on-app-origin', document.domain)"
SCRIPTED_HTML = f"<html><body><script>{MARK}</script></body></html>".encode()
SCRIPTED_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40">'
    '<rect width="40" height="40" fill="green"/>'
    f"<script>{MARK}</script></svg>"
).encode()


def _pdf_bytes():
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), "red").save(buffer, "PDF")
    return buffer.getvalue()


class _UploadsMixin:
    """A user with an Uploads folder, and the two ways the UI reaches a file."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="active-content")
        self.folder = FileService.create_folder(self.user, "Uploads")

    def _file(self, name, payload, mime_type):
        return FileService.create_file(
            owner=self.user,
            name=name,
            parent=self.folder,
            content=SimpleUploadedFile(name, payload, content_type=mime_type),
            mime_type=mime_type,
            acting_user=self.user,
        )

    def _content_url(self, file_obj):
        return f"{self.live_server_url}/api/v1/files/{file_obj.uuid}/content"

    def _open_in_viewer(self, file_obj):
        self.login_as(self.user)
        self.page.goto(
            f"{self.live_server_url}/files/{self.folder.uuid}?open={file_obj.uuid}"
        )
        viewer = self.page.locator("[data-file-viewer-inline]")
        expect(viewer.locator("h3")).to_have_text(file_obj.name)
        return viewer


class ContentActiveTypesTests(_UploadsMixin, PlaywrightTestCase):
    def _open_directly(self, url):
        """Navigate a tab to *url* as a link would; True when it downloaded."""
        try:
            with self.page.expect_download(timeout=5000):
                try:
                    self.page.goto(url)
                except PlaywrightError as exc:
                    if "Download is starting" not in str(exc):
                        raise
            return True
        except PlaywrightError:
            return False

    def _ran_on_app_origin(self):
        self.page.goto(f"{self.live_server_url}/files/{self.folder.uuid}")
        return self.page.evaluate("localStorage.getItem('ran-on-app-origin')")

    def test_opening_an_html_or_svg_content_url_runs_none_of_its_script(self):
        self.login_as(self.user)
        for name, payload, mime_type in [
            ("page.html", SCRIPTED_HTML, "text/html"),
            ("logo.svg", SCRIPTED_SVG, "image/svg+xml"),
        ]:
            with self.subTest(mime_type=mime_type):
                file_obj = self._file(name, payload, mime_type)

                self._open_directly(self._content_url(file_obj))

                self.assertIsNone(self._ran_on_app_origin())

    def test_a_plain_image_still_opens_in_the_tab(self):
        self.login_as(self.user)
        image = self._file("dot.png", PNG_1PX, "image/png")

        self.assertFalse(self._open_directly(self._content_url(image)))

        self.assertEqual(self.page.evaluate("document.images[0].naturalWidth"), 1)

    def test_the_image_viewer_draws_an_svg(self):
        svg = self._file("logo.svg", SCRIPTED_SVG, "image/svg+xml")

        viewer = self._open_in_viewer(svg)

        image = viewer.locator("img").first
        expect(image).to_be_visible()
        self.page.wait_for_function(
            "(img) => img.naturalWidth > 0", arg=image.element_handle()
        )
        self.assertEqual(image.evaluate("img => img.naturalWidth"), 40)
        # As an image an SVG runs no script, before the fix and after.
        self.assertIsNone(
            self.page.evaluate("localStorage.getItem('ran-on-app-origin')")
        )


class PdfViewerFramingTests(_UploadsMixin, PlaywrightTestCase):
    """The PDF viewer shows the content URL in an iframe.

    The headless shell Playwright launches by default has no PDF viewer and
    aborts a framed PDF, so this class runs the full Chromium build.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.browser.close()
        cls.browser = cls._playwright.chromium.launch(
            headless=cls.HEADLESS, channel="chromium"
        )

    def test_the_pdf_viewer_frames_the_content_url(self):
        pdf = self._file("report.pdf", _pdf_bytes(), "application/pdf")

        viewer = self._open_in_viewer(pdf)

        frame_element = viewer.locator(f'iframe[title="{pdf.name}"]')
        self.page.wait_for_function(
            """(title) => {
              const el = document.querySelector(`iframe[title="${title}"]`);
              return el && !Alpine.$data(el).loading;
            }""",
            arg=pdf.name,
        )
        # A frame refused by X-Frame-Options shows the browser's error page.
        frame = frame_element.element_handle().content_frame()
        self.assertEqual(frame.url, self._content_url(pdf))
        # The PDF viewer attaches its own frame after the iframe's load event.
        for _ in range(100):
            if any(f.url.startswith("chrome-extension://") for f in self.page.frames):
                break
            self.page.wait_for_timeout(100)
        else:
            self.fail("the PDF viewer did not load in the frame")
