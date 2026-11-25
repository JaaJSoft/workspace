"""E2E: the text viewer of a public share page saves a signed-in visitor's
editor preferences.

The share pages do not extend base.html, so the settings helper the viewer
writes through is only there when the share base loads it itself.
"""

from __future__ import annotations

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.services import tree
from workspace.users.services.settings import get_setting

from ._share_fixtures import build_shared_tree


class SharedTextEditorPrefsTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        build_shared_tree(self)
        self.text = tree.create_file(
            owner=self.owner,
            name="todo.txt",
            parent=self.root,
            content=SimpleUploadedFile(
                "todo.txt", b"buy milk\n", content_type="text/plain"
            ),
            acting_user=self.owner,
        )
        self.visitor = self.create_user(username="visitor")
        self.login_as(self.visitor)

    def tearDown(self):
        super().tearDown()
        cache.clear()

    def test_a_font_size_change_is_saved_for_the_visitor(self):
        self.page.goto(
            f"{self.live_server_url}/files/shared/{self.link.token}"
            f"?node={self.text.uuid}"
        )
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        expect(self.page.locator(".monaco-editor").first).to_be_visible()

        with self.page.expect_response(
            lambda r: (
                r.request.method == "PUT"
                and r.url.endswith("/api/v1/settings/files/editor")
            )
        ) as written:
            self.page.locator('[title="Increase font size"]').click()

        self.assertTrue(written.value.ok)
        self.assertEqual(get_setting(self.visitor, "files", "editor")["fontSize"], 15)
