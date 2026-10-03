"""E2E cover for the note keyboard shortcuts that reach into the page.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from django.core.files.base import ContentFile
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File


class NoteShortcutTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="note-keys")
        self.folder = File.objects.create(
            owner=self.user, name="Keys", node_type=File.NodeType.FOLDER
        )
        self.note = File.objects.create(
            owner=self.user,
            parent=self.folder,
            name="Groceries.md",
            node_type=File.NodeType.FILE,
            mime_type="text/markdown",
            type="markdown",
            content=ContentFile(b"- milk\n", name="Groceries.md"),
        )

    def test_r_focuses_and_selects_the_note_title(self):
        self.login_as(self.user)
        self.page.goto(
            f"{self.live_server_url}/notes?view=folder&folder={self.folder.uuid}"
        )
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        self.page.locator(f'button[data-note-uuid="{self.note.uuid}"]').click()
        title = self.page.locator('input[x-ref="noteTitle"]')
        expect(title).to_have_value("Groceries")
        # Readonly until the rename action arrives.
        expect(title).to_be_editable()
        # The editor mounts after the note opens; a key pressed before it is
        # up races its mount for the focus.
        expect(self.page.locator(".milkdown .ProseMirror")).to_be_visible()

        # Shortcuts are ignored while an editable element has the focus.
        self.page.evaluate("document.activeElement?.blur()")
        self.page.keyboard.press("r")

        expect(title).to_be_focused()
        self.assertEqual(
            self.page.evaluate(
                "() => { const el = document.activeElement;"
                " return el.value.slice(el.selectionStart, el.selectionEnd); }"
            ),
            "Groceries",
        )
