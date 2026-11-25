"""The text viewer arrives with the user's editor options, so opening a file
costs no settings request."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.urls import reverse

from workspace.files.services import tree
from workspace.files.ui.viewers import TextViewer
from workspace.users.services.settings import set_setting

User = get_user_model()


class TextViewerEditorPrefsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="editor_user", password="pw")
        self.client.force_login(self.user)
        self.file = tree.create_file(
            owner=self.user,
            name="script.py",
            content=SimpleUploadedFile("script.py", b"print('hi')\n"),
            acting_user=self.user,
        )

    def tearDown(self):
        cache.clear()

    def _open(self):
        return self.client.get(
            reverse("files_ui:view_file", kwargs={"uuid": self.file.uuid}),
            HTTP_X_ALPINE_REQUEST="true",
        )

    def test_the_saved_options_are_embedded_in_the_viewer(self):
        prefs = {"fontSize": 18, "minimap": False}
        set_setting(self.user, "files", "editor", prefs)

        resp = self._open()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["editor_prefs"], prefs)
        self.assertContains(resp, 'id="text-viewer-editor-prefs"')

    def test_a_user_who_never_saved_any_gets_none(self):
        resp = self._open()

        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context["editor_prefs"])

    def test_a_visitor_of_a_public_share_gets_none(self):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()

        html = TextViewer(self.file).render(request)

        self.assertIn(
            '<script id="text-viewer-editor-prefs" type="application/json">null</script>',
            html,
        )
