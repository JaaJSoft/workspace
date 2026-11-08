"""The viewer header's "Open in new tab": which viewers offer one, and where to.

A viewer offers a tab only for a file the browser shows on a page of its own;
the header reads the URL off the [data-viewer-new-tab] marker the viewer
renders (file_viewer.js). The public share page renders its header itself.
"""

import io
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from workspace.common.tests.pdf_fixtures import make_pdf
from workspace.files.models import FileShareLink
from workspace.files.services import FileService
from workspace.files.tests.ebooks import epub_bytes
from workspace.files.ui.viewers import (
    AudioViewer,
    EbookViewer,
    ImageViewer,
    MarkdownViewer,
    OfficeViewer,
    PDFViewer,
    TextViewer,
    VideoViewer,
)

User = get_user_model()
CONTENT = "/api/v1/files/0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b/content"


def _viewer(viewer_class, file_type="", name="file"):
    return viewer_class(SimpleNamespace(type=file_type, name=name))


class NewTabUrlTests(TestCase):
    def test_what_the_browser_shows_itself_opens_its_content(self):
        for viewer_class, file_type in [
            (PDFViewer, "pdf"),
            (ImageViewer, "jpeg"),
            (ImageViewer, "avif"),
            (VideoViewer, "mp4"),
            (AudioViewer, "mp3"),
        ]:
            with self.subTest(viewer=viewer_class.__name__, type=file_type):
                self.assertEqual(
                    _viewer(viewer_class, file_type).new_tab_url(CONTENT), CONTENT
                )

    def test_an_image_the_viewer_decodes_itself_offers_no_tab(self):
        for file_type in ("heif", "dng", "cr3"):
            with self.subTest(type=file_type):
                self.assertIsNone(_viewer(ImageViewer, file_type).new_tab_url(CONTENT))

    def test_what_a_tab_would_show_as_raw_bytes_offers_none(self):
        for viewer_class in (TextViewer, MarkdownViewer, OfficeViewer):
            with self.subTest(viewer=viewer_class.__name__):
                self.assertIsNone(_viewer(viewer_class).new_tab_url(CONTENT))

    def test_an_ebook_opens_the_reader_page(self):
        url = urlsplit(_viewer(EbookViewer, "epub", "a.epub").new_tab_url(CONTENT))

        self.assertEqual(url.path, reverse("files_ui:ebook_reader"))
        self.assertEqual(parse_qs(url.query), {"src": [CONTENT], "name": ["a.epub"]})


class NewTabMarkerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="newtab", password="x")
        self.client.force_login(self.user)

    def _file(self, name, data):
        return FileService.create_file(
            owner=self.user, name=name, content=ContentFile(data, name=name)
        )

    def _panel(self, file_obj):
        return self.client.get(
            reverse("files_ui:view_file", kwargs={"uuid": file_obj.uuid})
        ).content.decode()

    def test_the_panel_carries_the_url_of_a_viewer_that_offers_one(self):
        png = io.BytesIO()
        Image.new("RGB", (4, 4)).save(png, format="PNG")
        for name, data in [
            ("a.pdf", make_pdf(["page"])),
            ("a.png", png.getvalue()),
        ]:
            with self.subTest(name=name):
                file_obj = self._file(name, data)
                self.assertIn(
                    f'data-viewer-new-tab="/api/v1/files/{file_obj.uuid}/content"',
                    self._panel(file_obj),
                )

    def test_the_ebook_panel_carries_the_reader_url(self):
        file_obj = self._file("a.epub", epub_bytes())

        html = self._panel(file_obj)

        self.assertIn('data-viewer-new-tab="/files/reader?src=', html)

    def test_a_text_panel_carries_none(self):
        file_obj = self._file("a.txt", b"plain words\n")

        self.assertNotIn("data-viewer-new-tab", self._panel(file_obj))


class SharedPageNewTabTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="sharer", password="x")

    def _shared(self, name, data):
        file_obj = FileService.create_file(
            owner=self.owner, name=name, content=ContentFile(data, name=name)
        )
        link = FileShareLink.objects.create(
            file=file_obj, created_by=self.owner, mode=FileShareLink.Mode.READ
        )
        html = self.client.get(
            reverse("files_ui:shared_file", kwargs={"token": link.token})
        ).content.decode()
        return html, link

    def test_a_shared_pdf_offers_a_new_tab_on_the_link_s_content(self):
        html, link = self._shared("a.pdf", make_pdf(["page"]))

        self.assertRegex(
            html,
            rf'<a\s+href="/api/v1/files/shared/{link.token}/content[^"]*"\s+target="_blank"',
        )

    def test_a_shared_text_file_offers_none(self):
        html, _ = self._shared("a.txt", b"plain words\n")

        self.assertNotIn("New tab", html)
