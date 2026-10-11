"""E2E: a notes sidebar or menu update draws its own icons, not the page's.

Each test triggers one update path, waits for that path's own icons, and
checks the bystander icon of ``IconScopeMixin`` was left alone. Skipped
unless E2E=1.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.common.tests.e2e.icons import IconScopeMixin
from workspace.files.models import File


class NotesIconScopeTests(IconScopeMixin, PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="note-icons")
        self.parent = File.objects.create(
            owner=self.user, name="Parent", node_type=File.NodeType.FOLDER
        )
        self.child = File.objects.create(
            owner=self.user,
            parent=self.parent,
            name="Child",
            node_type=File.NodeType.FOLDER,
        )
        self.note = File.objects.create(
            owner=self.user,
            parent=self.parent,
            name="note.md",
            node_type=File.NodeType.FILE,
            mime_type="text/markdown",
            type="markdown",
        )
        self.login_as(self.user)

    def _folder_row(self, name):
        return self.page.locator("#notes-sidebar li").filter(
            has=self.page.get_by_text(name, exact=True)
        )

    def _folder_icon(self, name):
        return self._folder_row(name).locator('svg[data-lucide="folder"]')

    def _open(self, query=""):
        self.page.goto(f"{self.live_server_url}/notes{query}")
        expect(self._folder_icon("Parent")).to_be_attached()
        # The debug toolbar overlays the pane and swallows pointer events.
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        self.forget_bystander_redraws()

    def _ctx_menu_icon(self, label):
        return (
            self.page.locator("li")
            .filter(has=self.page.get_by_role("button", name=label, exact=True))
            .locator("svg[data-lucide]")
        )

    def test_loading_the_page_with_an_expanded_folder(self):
        self.page.goto(f"{self.live_server_url}/notes?expanded={self.parent.uuid}")

        expect(self._folder_icon("Child")).to_be_attached()
        self.assert_bystander_kept()

    def test_expanding_a_folder(self):
        self._open()
        self._folder_row("Parent").locator("button").last.click()

        expect(self._folder_icon("Child")).to_be_attached()
        self.assert_bystander_kept()

    def test_a_folder_context_menu(self):
        self._open()
        self._folder_row("Parent").locator("a").click(button="right")

        expect(self._ctx_menu_icon("Rename").first).to_be_attached()
        self.assert_bystander_kept()

    def test_a_note_context_menu(self):
        self._open(f"?view=folder&folder={self.parent.uuid}")
        self.page.locator(f'button[data-note-uuid="{self.note.uuid}"]').click(
            button="right"
        )

        expect(self._ctx_menu_icon("Rename").first).to_be_attached()
        self.assert_bystander_kept()

    def test_creating_a_subfolder(self):
        self._open()
        self._folder_row("Parent").locator("a").click(button="right")
        self.page.get_by_role("button", name="New subfolder").click()
        dialog = self.page.locator("#app-dialog-prompt")
        dialog.locator("input").fill("Fresh")
        dialog.get_by_role("button", name="Create").click()

        expect(self._folder_icon("Fresh")).to_be_attached()
        self.assert_bystander_kept()

    def test_a_preferences_change(self):
        self._open()
        self.page.evaluate(
            """() => window.dispatchEvent(new CustomEvent('notes:preferences-changed', {
              detail: JSON.parse(document.getElementById('notes-prefs-data').textContent),
            }))"""
        )

        self.assert_bystander_kept()
