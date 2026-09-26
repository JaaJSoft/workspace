"""Serving a file inline never holds the whole file in memory.

A gevent worker serves many requests at once, so one response buffering a
large log or CSV weighs on every request sharing that worker.
"""

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from rest_framework.test import APITestCase

from workspace.files.models import File, FileShareLink

User = get_user_model()

UTF8_TEXT = "Journal d'été\nligne 2 - café\n".encode()
NOT_UTF8_TEXT = b"caf\xe9 latin-1\n"


class InlineTextContentStreamingTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="streamer", email="streamer@example.com", password="pass123"
        )
        self.client.force_authenticate(user=self.user)

    def _text_file(self, name, payload, category="text"):
        return File.objects.create(
            owner=self.user,
            name=name,
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
            category=category,
            size=len(payload),
            content=ContentFile(payload, name=name),
        )

    def assert_streamed(self, resp, payload):
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.streaming)
        self.assertEqual(b"".join(resp.streaming_content), payload)
        self.assertEqual(resp["Content-Type"], "text/plain")

    def test_text_file_content_is_streamed(self):
        f = self._text_file("journal.log", UTF8_TEXT)
        resp = self.client.get(f"/api/v1/files/{f.uuid}/content")
        self.assert_streamed(resp, UTF8_TEXT)

    def test_code_file_content_is_streamed(self):
        f = self._text_file("script.py", UTF8_TEXT, category="code")
        resp = self.client.get(f"/api/v1/files/{f.uuid}/content")
        self.assert_streamed(resp, UTF8_TEXT)

    def test_text_file_that_is_not_utf8_keeps_its_bytes(self):
        f = self._text_file("legacy.txt", NOT_UTF8_TEXT)
        resp = self.client.get(f"/api/v1/files/{f.uuid}/content")
        self.assert_streamed(resp, NOT_UTF8_TEXT)

    def test_shared_text_file_content_is_streamed(self):
        f = self._text_file("journal.log", UTF8_TEXT)
        link = FileShareLink.objects.create(file=f, created_by=self.user)
        self.client.force_authenticate(user=None)
        resp = self.client.get(f"/api/v1/files/shared/{link.token}/content")
        self.assert_streamed(resp, UTF8_TEXT)

    def test_shared_text_file_with_a_newline_in_its_name_is_served(self):
        f = self._text_file('note";\r\nX-Injected: yes.txt', UTF8_TEXT)
        link = FileShareLink.objects.create(file=f, created_by=self.user)
        self.client.force_authenticate(user=None)
        resp = self.client.get(f"/api/v1/files/shared/{link.token}/content")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("\r", resp["Content-Disposition"])
        self.assertNotIn("\n", resp["Content-Disposition"])
        self.assertNotIn("X-Injected", resp)
