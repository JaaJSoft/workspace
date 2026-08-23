"""The office viewer end to end: viewer resolution to rendered editor frame."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from workspace.files.services import FileService

User = get_user_model()

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# An empty ZIP archive (a bare end-of-central-directory record). A docx is a
# zip container, so detection lands on the archive group rather than text -
# which is what decides the without-WOPI fallback behaviour.
EMPTY_ZIP = b"PK\x05\x06" + b"\x00" * 18


@override_settings(WOPI_DISCOVERY_URL="https://editor/hosting/discovery")
class OfficeViewerRenderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="office_user", email="office@test.com", password="pw"
        )
        self.client.force_login(self.user)
        self.file = FileService.create_file(
            owner=self.user,
            name="report.docx",
            content=SimpleUploadedFile(
                "report.docx", EMPTY_ZIP, content_type=DOCX_MIME
            ),
            acting_user=self.user,
        )

    def _view(self):
        return self.client.get(
            reverse("files_ui:view_file", kwargs={"uuid": self.file.uuid})
        )

    def test_renders_the_editor_frame_with_a_token(self):
        with patch(
            "workspace.files.services.wopi.discovery.get_action_url",
            return_value="https://editor/browser/abc/cool.html?",
        ) as mock_action:
            resp = self._view()
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("access_token", html)
        self.assertIn("WOPISrc=", html)
        # Owner can write, so the edit action is requested.
        mock_action.assert_called_once_with("docx", "edit")

    def test_office_file_is_viewable_in_the_api(self):
        self.file.refresh_from_db()
        self.assertTrue(self.file.is_viewable())

    def test_unreachable_editor_degrades_to_download(self):
        with patch(
            "workspace.files.services.wopi.discovery.get_action_url",
            return_value=None,
        ):
            resp = self._view()
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("Preview unavailable", html)
        self.assertIn(f"/api/v1/files/{self.file.uuid}/download", html)

    @override_settings(WOPI_DISCOVERY_URL="")
    def test_without_an_editor_the_file_is_not_viewable(self):
        self.file.refresh_from_db()
        self.assertFalse(self.file.is_viewable())
        resp = self._view()
        self.assertEqual(resp.status_code, 400)
        self.assertIn("No viewer available", resp.content.decode())
