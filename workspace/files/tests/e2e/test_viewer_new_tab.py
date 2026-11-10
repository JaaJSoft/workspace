"""E2E: the viewer header offers "Open in new tab" for what a tab shows.

The link lives in the header shared by every viewer, and appears once the
panel has merged and the action registry has answered: a PDF opens in the
browser's own reader, an e-book in the reader page, a text file gets none.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from django.core.files.base import ContentFile

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.common.tests.pdf_fixtures import make_pdf
from workspace.files.services import tree
from workspace.files.tests.ebooks import epub_bytes


class ViewerNewTabTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="newtab")
        self.login_as(self.user)

    def _open(self, name, data):
        file_obj = tree.create_file(
            owner=self.user, name=name, content=ContentFile(data, name=name)
        )
        self.page.goto(f"{self.live_server_url}/files?open={file_obj.uuid}")
        self.page.locator("#viewer-panel").first.wait_for(state="attached")
        return file_obj

    def _link(self):
        return self.page.locator("[data-file-viewer-inline] [data-viewer-new-tab-link]")

    def test_a_pdf_opens_its_content_in_a_new_tab(self):
        file_obj = self._open("report.pdf", make_pdf(["quarterly budget"]))

        link = self._link()
        link.wait_for(state="visible", timeout=10_000)
        self.assertEqual(
            link.get_attribute("href"), f"/api/v1/files/{file_obj.uuid}/content"
        )
        self.assertEqual(link.get_attribute("target"), "_blank")

    def test_an_ebook_opens_the_reader_in_a_new_tab(self):
        self._open("book.epub", epub_bytes())

        link = self._link()
        link.wait_for(state="visible", timeout=10_000)
        self.assertTrue(link.get_attribute("href").startswith("/files/reader?src="))

    def test_a_text_file_offers_no_new_tab(self):
        self._open("notes.txt", b"plain words\n")
        self.page.locator("#viewer-panel [data-viewer-location]").wait_for(
            state="attached", timeout=10_000
        )
        self.page.wait_for_timeout(300)

        self.assertFalse(self._link().is_visible())
