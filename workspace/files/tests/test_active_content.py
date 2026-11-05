"""Stored bytes never run as a page of the app.

The content endpoints serve a file with the MIME type it was stored with, from
the app's own origin. A browser opening one of those URLs directly renders an
HTML, XHTML, SVG or XML file as a document, and its scripts run with the
viewer's session. Such a type must download, and a download keeps the
project's refusal to be framed; every other type is sandboxed, except the ones
the browser plays in its own viewer.
"""

import base64
import io

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File, FileShareLink
from workspace.files.services import FileService

User = get_user_model()

SCRIPTED = b"<html><body><script>fetch('/api/v1/users/me')</script></body></html>"

ACTIVE_TYPES = [
    "text/html",
    "application/xhtml+xml",
    "image/svg+xml",
    "text/xml",
    "application/xml",
    "application/rss+xml",
    "text/xsl",
    "Text/HTML; charset=utf-8",
    # A browser reads the last type of a comma-separated Content-Type.
    "image/png, text/html",
]
# Rendered as passive documents: shown inline, behind the sandbox.
SANDBOXED_INLINE_TYPES = ["image/png", "image/jpeg", "text/plain", "application/json"]
# Played by the browser's own viewer, which the sandbox would break.
PLAYED_TYPES = ["application/pdf", "video/mp4", "audio/ogg"]


def assert_sandboxed(test, response):
    policy = response.get("Content-Security-Policy")
    test.assertIsNotNone(policy, "no Content-Security-Policy")
    directives = {d.strip() for d in policy.split(";")}
    # A bare sandbox: no allow-scripts, no allow-same-origin.
    test.assertIn("sandbox", directives)
    test.assertIn("default-src 'none'", directives)


def assert_disposition(test, response, kind):
    test.assertEqual(response.get("Content-Disposition", "").split(";", 1)[0], kind)


def consume(response):
    if hasattr(response, "streaming_content"):
        b"".join(response.streaming_content)


class FileContentActiveTypesTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="active", password="pass")
        self.client.force_authenticate(self.user)
        self._count = 0

    def _file(self, mime_type, payload=SCRIPTED):
        self._count += 1
        file_obj = File(
            owner=self.user,
            name=f"file-{self._count}",
            node_type=File.NodeType.FILE,
            mime_type=mime_type,
            size=len(payload),
        )
        file_obj.content = ContentFile(payload, name="blob.bin")
        file_obj.save()
        return file_obj

    def _content(self, file_obj, **headers):
        response = self.client.get(f"/api/v1/files/{file_obj.uuid}/content", **headers)
        consume(response)
        return response

    def test_an_active_type_downloads_and_is_sandboxed(self):
        for mime_type in ACTIVE_TYPES:
            with self.subTest(mime_type=mime_type):
                response = self._content(self._file(mime_type))
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                assert_disposition(self, response, "attachment")
                assert_sandboxed(self, response)
                self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_an_active_type_downloads_on_a_range_request(self):
        for mime_type in ACTIVE_TYPES:
            with self.subTest(mime_type=mime_type):
                response = self._content(self._file(mime_type), HTTP_RANGE="bytes=0-9")
                self.assertEqual(response.status_code, status.HTTP_206_PARTIAL_CONTENT)
                assert_disposition(self, response, "attachment")
                assert_sandboxed(self, response)

    def test_a_revalidation_replaces_the_headers_of_a_cached_copy(self):
        # A copy cached before this policy is revalidated on the next visit;
        # the browser takes Content-Disposition and the policy from the 304.
        file_obj = self._file("image/svg+xml")
        etag = self._content(file_obj)["ETag"]

        response = self._content(file_obj, HTTP_IF_NONE_MATCH=etag)

        self.assertEqual(response.status_code, status.HTTP_304_NOT_MODIFIED)
        assert_disposition(self, response, "attachment")
        assert_sandboxed(self, response)

    def test_a_plain_image_stays_inline_behind_the_sandbox(self):
        for mime_type in SANDBOXED_INLINE_TYPES:
            with self.subTest(mime_type=mime_type):
                response = self._content(self._file(mime_type, payload=b"bytes"))
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                assert_disposition(self, response, "inline")
                assert_sandboxed(self, response)

    def test_a_type_the_browser_plays_itself_is_inline_and_not_sandboxed(self):
        for mime_type in PLAYED_TYPES:
            with self.subTest(mime_type=mime_type):
                response = self._content(self._file(mime_type, payload=b"bytes"))
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                assert_disposition(self, response, "inline")
                self.assertNotIn("Content-Security-Policy", response)
                self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_a_forced_download_is_not_for_framing(self):
        for headers in ({}, {"HTTP_RANGE": "bytes=0-9"}):
            with self.subTest(headers=headers):
                response = self._content(self._file("text/html"), **headers)
                self.assertEqual(response["X-Frame-Options"], "DENY")


class SharedFileContentActiveTypesTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="sharer", password="pass")

    def _link(self, name, mime_type, payload=SCRIPTED):
        file_obj = File.objects.create(
            owner=self.owner,
            name=name,
            node_type=File.NodeType.FILE,
            mime_type=mime_type,
            size=len(payload),
            content=ContentFile(payload, name=name),
        )
        return FileShareLink.objects.create(
            file=file_obj, created_by=self.owner, mode=FileShareLink.Mode.READ
        )

    def _content(self, link, **headers):
        response = self.client.get(
            f"/api/v1/files/shared/{link.token}/content", **headers
        )
        consume(response)
        return response

    def test_a_shared_svg_downloads_and_is_sandboxed(self):
        link = self._link("logo.svg", "image/svg+xml")
        for headers in ({}, {"HTTP_RANGE": "bytes=0-9"}):
            with self.subTest(headers=headers):
                response = self._content(link, **headers)
                self.assertIn(response.status_code, (200, 206))
                assert_disposition(self, response, "attachment")
                assert_sandboxed(self, response)

    def test_a_shared_image_stays_inline_behind_the_sandbox(self):
        response = self._content(self._link("photo.png", "image/png", b"png"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        assert_disposition(self, response, "inline")
        assert_sandboxed(self, response)
        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")

    def test_a_shared_pdf_can_be_framed_by_the_share_page(self):
        response = self._content(self._link("doc.pdf", "application/pdf", b"%PDF"))
        assert_disposition(self, response, "inline")
        self.assertNotIn("Content-Security-Policy", response)
        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")


def _basic_auth(username, password):
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


class WebDAVActiveTypesTests(TestCase):
    """WebDAV serves the same bytes on the same origin, outside Django's
    middleware, to a browser holding the Basic credentials."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from workspace.files.webdav.app import create_webdav_app

        cls._app = create_webdav_app()

    def setUp(self):
        self.user = User.objects.create_user(username="davactive", password="pass123")

    def _get(self, path, method="GET", **extra):
        env = {
            "REQUEST_METHOD": method,
            "SCRIPT_NAME": "/dav",
            "PATH_INFO": path,
            "SERVER_NAME": "testserver",
            "SERVER_PORT": "80",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "HTTP_HOST": "testserver",
            "HTTP_AUTHORIZATION": _basic_auth("davactive", "pass123"),
            "wsgi.input": io.BytesIO(b""),
            "wsgi.errors": io.BytesIO(),
            "wsgi.url_scheme": "http",
            "CONTENT_LENGTH": "0",
            **extra,
        }
        captured = {}

        def start_response(status_line, headers, exc_info=None):
            captured["status"] = int(status_line.split(" ", 1)[0])
            captured["headers"] = {name.lower(): value for name, value in headers}

        result = self._app(env, start_response)
        b"".join(result)
        if hasattr(result, "close"):
            result.close()
        return captured["status"], captured["headers"]

    def _create(self, name, mime_type, payload=SCRIPTED):
        FileService.create_file(
            self.user,
            name,
            content=ContentFile(payload, name=name),
            mime_type=mime_type,
        )

    def _assert_sandboxed(self, headers):
        directives = {
            d.strip() for d in headers.get("content-security-policy", "").split(";")
        }
        self.assertIn("sandbox", directives)
        self.assertIn("default-src 'none'", directives)

    def test_an_html_file_downloads_and_is_sandboxed(self):
        self._create("page.html", "text/html")
        for method in ("GET", "HEAD"):
            with self.subTest(method=method):
                code, headers = self._get("/page.html", method)
                self.assertEqual(code, 200)
                self.assertEqual(
                    headers.get("content-disposition", "").split(";", 1)[0],
                    "attachment",
                )
                self._assert_sandboxed(headers)
                self.assertEqual(headers.get("x-content-type-options"), "nosniff")

    def test_an_image_is_not_forced_to_download(self):
        self._create("photo.png", "image/png", b"png")
        code, headers = self._get("/photo.png")
        self.assertEqual(code, 200)
        self.assertNotIn("content-disposition", headers)
        self._assert_sandboxed(headers)

    def test_a_folder_listing_is_left_alone(self):
        FileService.create_folder(self.user, "Docs")
        code, headers = self._get("/Docs/", "PROPFIND", HTTP_DEPTH="1")
        self.assertEqual(code, 207)
        self.assertNotIn("content-security-policy", headers)
