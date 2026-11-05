import io

from django.http import HttpResponse
from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory

from workspace.common.http_ranges import serve_with_ranges
from workspace.common.stored_content import (
    STORED_CONTENT_CSP,
    is_active_content,
    protect_stored_content,
)


class IsActiveContentTests(SimpleTestCase):
    def test_types_a_browser_runs_as_a_page(self):
        for content_type in [
            "text/html",
            "application/xhtml+xml",
            "image/svg+xml",
            "text/xml",
            "application/xml",
            "text/xsl",
            "application/atom+xml",
            "application/vnd.custom+xml",
        ]:
            with self.subTest(content_type=content_type):
                self.assertTrue(is_active_content(content_type))

    def test_case_parameters_and_whitespace_do_not_hide_one(self):
        for content_type in [
            "TEXT/HTML",
            "text/html; charset=utf-8",
            "  image/svg+xml  ",
            "Image/SVG+XML;q=1",
        ]:
            with self.subTest(content_type=content_type):
                self.assertTrue(is_active_content(content_type))

    def test_a_comma_does_not_hide_one(self):
        self.assertTrue(is_active_content("image/png, text/html"))
        self.assertTrue(is_active_content("text/html,image/png"))

    def test_passive_types(self):
        for content_type in [
            "image/png",
            "text/plain",
            "application/pdf",
            "video/mp4",
            "application/json",
            "application/octet-stream",
            "",
            None,
        ]:
            with self.subTest(content_type=content_type):
                self.assertFalse(is_active_content(content_type))


class ProtectStoredContentTests(SimpleTestCase):
    def _headers(self, content_type):
        return protect_stored_content(HttpResponse(), content_type)

    def test_a_passive_document_is_sandboxed(self):
        for content_type in ["image/png", "text/plain", "text/html", "", None]:
            with self.subTest(content_type=content_type):
                response = self._headers(content_type)
                self.assertEqual(
                    response["Content-Security-Policy"], STORED_CONTENT_CSP
                )
                self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_a_type_the_browser_plays_is_left_unsandboxed(self):
        for content_type in ["application/pdf", "video/webm", "audio/mpeg"]:
            with self.subTest(content_type=content_type):
                response = self._headers(content_type)
                self.assertNotIn("Content-Security-Policy", response)
                self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_a_played_type_next_to_another_is_sandboxed(self):
        response = self._headers("video/mp4, text/html")
        self.assertEqual(response["Content-Security-Policy"], STORED_CONTENT_CSP)


class ServeWithRangesActiveContentTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def _serve(self, content_type, range_header=None, **names):
        request = self.factory.get("/whatever")
        if range_header:
            request.META["HTTP_RANGE"] = range_header
        return serve_with_ranges(
            request,
            file_handle=io.BytesIO(b"<svg></svg>"),
            file_size=11,
            content_type=content_type,
            **names,
        )

    def test_an_active_type_asked_inline_downloads(self):
        for range_header in (None, "bytes=0-3"):
            with self.subTest(range_header=range_header):
                response = self._serve(
                    "image/svg+xml", range_header, inline_filename="logo.svg"
                )
                self.assertTrue(
                    response["Content-Disposition"].startswith("attachment")
                )
                self.assertIn("logo.svg", response["Content-Disposition"])
                self.assertEqual(
                    response["Content-Security-Policy"], STORED_CONTENT_CSP
                )

    def test_an_image_asked_inline_stays_inline(self):
        for range_header in (None, "bytes=0-3"):
            with self.subTest(range_header=range_header):
                response = self._serve(
                    "image/png", range_header, inline_filename="photo.png"
                )
                self.assertTrue(response["Content-Disposition"].startswith("inline"))
                self.assertEqual(
                    response["Content-Security-Policy"], STORED_CONTENT_CSP
                )
                self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")

    def test_a_video_stays_playable(self):
        response = self._serve("video/mp4", inline_filename="clip.mp4")
        self.assertTrue(response["Content-Disposition"].startswith("inline"))
        self.assertNotIn("Content-Security-Policy", response)

    def test_an_active_type_downloads_without_a_filename(self):
        for range_header in (None, "bytes=0-3"):
            with self.subTest(range_header=range_header):
                response = self._serve("text/html", range_header)
                self.assertTrue(
                    response["Content-Disposition"].startswith("attachment")
                )
