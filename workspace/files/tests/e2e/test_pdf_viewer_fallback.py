"""E2E: a browser that cannot show a PDF inside the page gets a card instead.

Chrome and Firefox on Android report ``navigator.pdfViewerEnabled === false``
and paint their own grey placeholder with a bare "Open" button in a framed
PDF. The viewer offers the file's own card there, with Open and Download.

The property is pinned with an init script on both sides, so the tests do not
depend on whether the Chromium build Playwright launched carries a PDF viewer.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from django.core.files.base import ContentFile
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.common.tests.pdf_fixtures import make_pdf
from workspace.files.services import tree


def _pin_pdf_viewer_enabled(page, enabled):
    page.add_init_script(
        "Object.defineProperty(Navigator.prototype, 'pdfViewerEnabled',"
        f" {{ get: () => {'true' if enabled else 'false'} }});"
    )


class PdfViewerFallbackTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="pdfmobile")
        self.login_as(self.user)
        self.pdf = tree.create_file(
            owner=self.user,
            name="report.pdf",
            content=ContentFile(make_pdf(["quarterly budget"]), name="report.pdf"),
        )

    def _open(self):
        self.page.goto(f"{self.live_server_url}/files?open={self.pdf.uuid}")
        self.page.locator("#viewer-panel").first.wait_for(state="attached")

    def test_without_an_inline_reader_the_viewer_offers_open_and_download(self):
        _pin_pdf_viewer_enabled(self.page, False)
        self._open()

        card = self.page.locator("#viewer-panel [data-pdf-unavailable]")
        expect(card).to_be_visible(timeout=10_000)
        expect(card).to_contain_text("report.pdf")
        expect(card).to_contain_text("This browser can't display PDFs here")
        content_url = f"/api/v1/files/{self.pdf.uuid}/content"
        open_link = card.get_by_role("link", name="Open")
        expect(open_link).to_have_attribute("href", content_url)
        expect(open_link).to_have_attribute("target", "_blank")
        expect(card.get_by_role("link", name="Download")).to_have_attribute(
            "download", "report.pdf"
        )
        expect(self.page.locator("#viewer-panel iframe")).to_have_count(0)

    def test_with_an_inline_reader_the_pdf_is_framed(self):
        _pin_pdf_viewer_enabled(self.page, True)
        self._open()

        expect(
            self.page.locator('#viewer-panel iframe[title="report.pdf"]')
        ).to_be_attached(timeout=10_000)
        expect(self.page.locator("#viewer-panel [data-pdf-unavailable]")).to_have_count(
            0
        )
