"""The file viewer endpoint must wrap every response in the #viewer-panel anchor.

The viewer surfaces (files viewer modal, notes editor pane) load
``/files/view/<uuid>`` through alpine-ajax, which merges responses by element
id: a body without the anchor never renders. Error bodies are wrapped too, so
"no viewer available" lands inside the panel instead of vanishing.
"""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from workspace.files.services import FileService
from workspace.files.ui.viewers import VIEWER_PANEL_ID

User = get_user_model()

PANEL_OPENING = f'<div id="{VIEWER_PANEL_ID}"'


class ViewerPanelWrapperTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="panel_user", email="panel@test.com", password="pw"
        )
        self.client.force_login(self.user)

    def _get(self, file_obj):
        return self.client.get(
            reverse("files_ui:view_file", kwargs={"uuid": file_obj.uuid})
        )

    def test_a_rendered_viewer_is_wrapped_in_the_panel(self):
        f = FileService.create_file(
            owner=self.user,
            name="note.txt",
            content=SimpleUploadedFile(
                "note.txt", b"hello\n" * 40, content_type="text/plain"
            ),
            acting_user=self.user,
        )
        resp = self._get(f)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertTrue(html.startswith(PANEL_OPENING), html[:120])
        self.assertTrue(html.endswith("</div>"))

    def test_an_error_body_is_wrapped_in_the_panel(self):
        """A 400 must still merge into the panel, not vanish client-side."""
        folder = FileService.create_folder(
            owner=self.user, name="Docs", acting_user=self.user
        )
        resp = self._get(folder)
        self.assertEqual(resp.status_code, 400)
        self.assertIn(PANEL_OPENING, resp.content.decode())
