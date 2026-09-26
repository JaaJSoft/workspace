"""The text and Markdown viewers embed the file in the page only up to a cap.

Past it, the page would carry the whole file (twice, once escaped for JS), and
the worker would read it whole to build that page.
"""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from workspace.files.models import FileShareLink
from workspace.files.services import FileService

User = get_user_model()

MARKER = "needleline"  # no character escapejs rewrites
PAYLOAD = (MARKER + "\n").encode() * 200  # 2.4 KB


@override_settings(FILES_TEXT_VIEWER_MAX_BYTES=1024)
class ViewerSizeCapTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="capper", password="pw")
        self.client.force_login(self.user)

    def _file(self, name, payload=PAYLOAD):
        return FileService.create_file(
            owner=self.user,
            name=name,
            content=SimpleUploadedFile(name, payload, content_type="text/plain"),
            acting_user=self.user,
        )

    def _view(self, file_obj):
        resp = self.client.get(
            reverse("files_ui:view_file", kwargs={"uuid": file_obj.uuid})
        )
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def test_a_large_text_file_is_not_embedded(self):
        f = self._file("server.log")
        html = self._view(f)
        self.assertNotIn(MARKER, html)
        self.assertIn(f"/api/v1/files/{f.uuid}/content", html)

    def test_a_large_markdown_file_is_not_embedded(self):
        f = self._file("notes.md", b"# Title\n\n" + PAYLOAD)
        html = self._view(f)
        self.assertNotIn(MARKER, html)
        self.assertIn(f"/api/v1/files/{f.uuid}/content", html)

    def test_a_text_file_under_the_cap_is_embedded(self):
        f = self._file("small.log", (MARKER + "\n").encode())
        self.assertIn(MARKER, self._view(f))

    def test_the_share_page_links_to_the_shared_content(self):
        f = self._file("server.log")
        link = FileShareLink.objects.create(file=f, created_by=self.user)
        self.client.logout()
        resp = self.client.get(f"/files/shared/{link.token}")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertNotIn(MARKER, html)
        self.assertIn(f"/api/v1/files/shared/{link.token}/content", html)
