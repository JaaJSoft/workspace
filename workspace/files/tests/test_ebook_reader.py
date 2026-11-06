"""The e-book viewer and the reader page it frames.

What runs in the browser (the book opening, its scripts staying inert) is
covered by e2e/test_ebook_reader.py; this pins the server half that makes it
safe: the reader page's policy, and the URLs it accepts.
"""

from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import RequestFactory, TestCase
from django.urls import reverse

from workspace.files.services import FileService
from workspace.files.tests.ebooks import epub_bytes
from workspace.files.ui.viewers import EbookViewer

User = get_user_model()

READER_URL = reverse("files_ui:ebook_reader")
CONTENT_URL = "/api/v1/files/0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b/content"


def _directives(response):
    policy = response["Content-Security-Policy"]
    return {
        name: values
        for name, *values in (
            directive.split() for directive in policy.split(";") if directive.strip()
        )
    }


class ReaderPageTests(TestCase):
    def test_it_renders_for_a_path_on_the_site(self):
        response = self.client.get(READER_URL, {"src": CONTENT_URL, "name": "a.epub"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'data-src="{CONTENT_URL}"')
        self.assertContains(response, 'data-name="a.epub"')

    def test_no_script_but_the_site_s_own_files_may_run(self):
        response = self.client.get(READER_URL, {"src": CONTENT_URL})

        directives = _directives(response)
        self.assertEqual(directives["script-src"], ["'self'"])
        self.assertEqual(directives["default-src"], ["'none'"])
        self.assertEqual(directives["frame-ancestors"], ["'self'"])

    def test_the_viewer_panel_may_frame_it(self):
        response = self.client.get(READER_URL, {"src": CONTENT_URL})

        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")

    def test_a_source_off_the_site_is_not_found(self):
        for src in (
            "",
            "//evil.example/book.epub",
            "/\\evil.example/book.epub",
            "https://evil.example/book.epub",
            "javascript:alert(1)",
            "api/v1/files/x/content",
            "/" + "a" * 2048,
        ):
            with self.subTest(src=src):
                response = self.client.get(READER_URL, {"src": src})
                self.assertEqual(response.status_code, 404)

    def test_the_name_is_escaped_into_the_page(self):
        response = self.client.get(
            READER_URL, {"src": CONTENT_URL, "name": '"><script>x()</script>.epub'}
        )

        self.assertNotContains(response, "<script>x()</script>")


class EbookViewerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="reader", password="pass")
        self.file = FileService.create_file(
            owner=self.user,
            name="Long & winding.epub",
            content=ContentFile(epub_bytes(), name="book.epub"),
        )

    def test_an_epub_resolves_to_the_ebook_viewer(self):
        self.assertEqual(self.file.type, "epub")
        self.assertTrue(self.file.is_viewable())

    def test_it_frames_the_reader_on_the_file_s_content(self):
        request = RequestFactory().get("/")
        request.user = self.user

        html = EbookViewer(self.file).render(request)

        reader_url = EbookViewer(self.file).get_context(request)["reader_url"]
        url = urlsplit(reader_url)
        self.assertEqual(url.path, READER_URL)
        self.assertEqual(
            parse_qs(url.query),
            {
                "src": [f"/api/v1/files/{self.file.uuid}/content"],
                "name": ["Long & winding.epub"],
            },
        )
        self.assertIn("data-ebook-reader", html)
        self.assertIn(reader_url.replace("&", "&amp;"), html)

    def test_it_reads_the_content_url_the_host_hands_it(self):
        request = RequestFactory().get("/")
        request.user = self.user
        viewer = EbookViewer(self.file)
        viewer._content_url = "/s/token/content"

        query = parse_qs(urlsplit(viewer.get_context(request)["reader_url"]).query)

        self.assertEqual(query["src"], ["/s/token/content"])

    def test_the_reader_opens_from_the_viewer_panel(self):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("files_ui:view_file", kwargs={"uuid": self.file.uuid})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-ebook-reader")
