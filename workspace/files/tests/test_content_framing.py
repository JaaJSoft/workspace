"""Who may show a file's bytes in a frame.

The PDF viewer draws the browser's own reader in an <iframe> pointed at the
content endpoint. The project answers ``X-Frame-Options: DENY`` everywhere
else, so a content response that kept that default would leave the viewer an
empty frame. Content served for viewing lets this app's pages frame it, and
nothing else: other origins still may not, and a download is not for framing.
"""

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.files.models import File, FileShareLink

User = get_user_model()

PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


class ContentFramingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="framer", password="pass123")
        self.client.force_login(self.user)
        self.pdf = File(
            owner=self.user,
            name="report.pdf",
            node_type=File.NodeType.FILE,
            mime_type="application/pdf",
            size=len(PDF),
        )
        self.pdf.content = ContentFile(PDF, name="report.pdf")
        self.pdf.save()

    def test_content_may_be_framed_by_this_app(self):
        response = self.client.get(f"/api/v1/files/{self.pdf.uuid}/content")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")

    def test_content_range_may_be_framed_by_this_app(self):
        response = self.client.get(
            f"/api/v1/files/{self.pdf.uuid}/content", HTTP_RANGE="bytes=0-9"
        )
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")

    def test_shared_link_content_may_be_framed_by_this_app(self):
        link = FileShareLink.objects.create(file=self.pdf, created_by=self.user)
        self.client.logout()
        response = self.client.get(f"/api/v1/files/shared/{link.token}/content")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")

    def test_download_is_not_for_framing(self):
        response = self.client.get(f"/api/v1/files/{self.pdf.uuid}/download")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Frame-Options"], "DENY")
