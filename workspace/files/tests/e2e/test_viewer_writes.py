"""E2E: what an editor saves reaches the server.

The image, text and markdown editors write through the API with the session
cookie, so each write must carry the CSRF token: the server refuses one
without it with a 403.
"""

from __future__ import annotations

import base64

from django.core.cache import cache
from django.core.files.base import ContentFile
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.services import tree

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
VIEWER = "[data-file-viewer-inline]"


class ViewerWriteTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user(username="writer")
        self.login_as(self.owner)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _open(self, name, body, mime_type):
        file_obj = tree.create_file(
            self.owner,
            name,
            content=ContentFile(body, name=name),
            mime_type=mime_type,
        )
        self.page.goto(f"{self.live_server_url}/files?open={file_obj.uuid}")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        viewer = self.page.locator(VIEWER)
        expect(viewer.locator("h3")).to_have_text(name)
        return file_obj, viewer

    def _assert_write_accepted(self, response_info):
        response = response_info.value
        self.assertEqual(response.status, 200, response.url)
        cookie = next(
            c["value"] for c in self.context.cookies() if c["name"] == "csrftoken"
        )
        self.assertEqual(response.request.headers.get("x-csrftoken"), cookie)

    def _is_write(self, file_obj, path=""):
        url = f"/api/v1/files/{file_obj.uuid}{path}"
        return lambda r: (
            r.request.method in ("PATCH", "PUT") and r.url.split("?")[0].endswith(url)
        )

    def test_an_image_edit_is_saved(self):
        file_obj, viewer = self._open("photo.png", PNG_1PX, "image/png")

        viewer.get_by_test_id("image-edit-start").click()
        viewer.get_by_test_id("image-edit-rotate-right").click()
        with self.page.expect_response(
            self._is_write(file_obj, "/image-edit")
        ) as response:
            viewer.get_by_test_id("image-edit-save").click()

        self._assert_write_accepted(response)

    def test_a_text_edit_is_saved(self):
        file_obj, viewer = self._open("todo.txt", b"buy milk\n", "text/plain")
        editor = viewer.locator(".monaco-editor textarea").first
        expect(viewer.locator(".monaco-editor").first).to_be_visible(timeout=20000)

        editor.focus()
        self.page.keyboard.type("eggs ")
        with self.page.expect_response(self._is_write(file_obj)) as response:
            self.page.keyboard.press("Control+s")

        self._assert_write_accepted(response)

    def test_a_markdown_edit_is_saved(self):
        file_obj, viewer = self._open("draft.md", b"# Draft\n\nText\n", "text/markdown")
        editor = viewer.locator(".ProseMirror").first
        expect(editor).to_be_visible(timeout=20000)

        editor.click()
        self.page.keyboard.press("End")
        self.page.keyboard.type(" more")
        with self.page.expect_response(self._is_write(file_obj)) as response:
            self.page.keyboard.press("Control+s")

        self._assert_write_accepted(response)
