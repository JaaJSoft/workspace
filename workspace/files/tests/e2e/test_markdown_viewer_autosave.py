"""E2E: opening a note in the markdown editor must not save it.

The editor's change listener receives the editor context first and the
markdown second. Reading the first argument as the markdown made every
document update look like an edit, and Crepe updates the document on its own
once the note is open when the last block is not a paragraph (a code block, a
table, a file embed): every opening of such a note saved it again, bumping its
modification date and notifying its owner.
"""

from __future__ import annotations

from django.core.files.base import ContentFile
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.services import FileService


class MarkdownViewerOpenTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="opener")
        self.login_as(self.user)

    def _assert_opening_saves_nothing(self, body):
        note = FileService.create_file(
            self.user,
            "Ends.md",
            content=ContentFile(body.encode(), name="Ends.md"),
            mime_type="text/markdown",
        )
        writes = []
        self.page.on(
            "request",
            lambda r: (
                writes.append(r.url)
                if r.method in ("PATCH", "PUT") and str(note.uuid) in r.url
                else None
            ),
        )
        self.page.goto(f"{self.live_server_url}/notes?file={note.uuid}")
        expect(self.page.locator(".ProseMirror").first).to_be_visible(timeout=20000)
        # Past the 2s autosave debounce.
        self.page.wait_for_timeout(3500)
        self.assertEqual(writes, [])
        expect(
            self.page.locator("#viewer-panel").get_by_text("Modified", exact=True)
        ).to_be_hidden()

    def test_note_ending_with_a_code_block(self):
        self._assert_opening_saves_nothing("# Code\n\n```py\nprint(1)\n```\n")

    def test_note_ending_with_a_file_embed(self):
        pdf = FileService.create_file(
            self.user,
            "report.pdf",
            content=ContentFile(b"%PDF-1.4\n%%EOF\n", name="report.pdf"),
            mime_type="application/pdf",
        )
        self._assert_opening_saves_nothing(
            f"# Report\n\n![report.pdf](/api/v1/files/{pdf.uuid}/content)\n"
        )
