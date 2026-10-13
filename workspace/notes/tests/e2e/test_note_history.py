"""E2E: opening a note is one browser history entry, closing it undoes it.

Opening a note from the list adds an entry; the back arrow, Escape and the
browser's back all return to the list, and the browser's back from the list
leaves the notes. A back or forward never adds entries of its own, and a deep
link to a note is the entry the page landed on, not one more.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from django.core.files.base import ContentFile
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File

MOBILE = {"width": 390, "height": 844}


class NoteHistoryTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="note-history")
        self.folder = File.objects.create(
            owner=self.user, name="Errands", node_type=File.NodeType.FOLDER
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
        self.login_as(self.user)
        self.before_url = f"{self.live_server_url}/files"
        self.list_url = (
            f"{self.live_server_url}/notes?view=folder&folder={self.folder.uuid}"
        )
        self.note_url = f"{self.list_url}&file={self.note.uuid}"
        self.title = self.page.locator('input[x-ref="noteTitle"]')
        self.row = self.page.locator(f'button[data-note-uuid="{self.note.uuid}"]')
        self.back_arrow = self.page.get_by_role("button", name="Back to the list")

    def _visit(self, url):
        # A page of the app before the notes, so a back from the list has
        # somewhere to land.
        self.page.goto(self.before_url)
        self.page.goto(url)
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")

    def _open_note_from_the_list(self):
        self.row.click()
        expect(self.title).to_have_value("Groceries")
        expect(self.page).to_have_url(self.note_url)

    def _expect_the_list(self):
        expect(self.page).to_have_url(self.list_url)
        expect(self.row).to_be_visible()

    def _history_length(self):
        return self.page.evaluate("history.length")

    def test_mobile_back_arrow_returns_to_the_list_and_back_leaves_the_notes(self):
        self.page.set_viewport_size(MOBILE)
        self._visit(self.list_url)
        self._open_note_from_the_list()

        self.back_arrow.click()

        self._expect_the_list()
        self.page.go_back()
        expect(self.page).to_have_url(self.before_url)

    def test_mobile_deep_link_back_arrow_lands_on_the_list_without_a_duplicate(self):
        self.page.set_viewport_size(MOBILE)
        self._visit(self.note_url)
        expect(self.title).to_have_value("Groceries")
        length = self._history_length()

        self.back_arrow.click()

        self._expect_the_list()
        self.assertEqual(self._history_length(), length)
        self.page.go_back()
        expect(self.page).to_have_url(self.before_url)

    def test_mobile_back_and_forward_toggle_the_note_without_growing_history(self):
        self.page.set_viewport_size(MOBILE)
        self._visit(self.list_url)
        self._open_note_from_the_list()
        length = self._history_length()

        self.page.go_back()
        self._expect_the_list()
        self.page.go_forward()
        expect(self.page).to_have_url(self.note_url)
        expect(self.title).to_be_visible()
        self.page.go_back()

        self._expect_the_list()
        self.assertEqual(self._history_length(), length)

    def test_mobile_escape_closes_the_note_like_the_back_arrow(self):
        self.page.set_viewport_size(MOBILE)
        self._visit(self.list_url)
        self._open_note_from_the_list()
        # The editor takes the focus once mounted, and Escape in an editable
        # element only blurs it.
        expect(self.page.locator(".milkdown .ProseMirror")).to_be_visible()
        self.page.evaluate("document.activeElement?.blur()")

        self.page.keyboard.press("Escape")

        self._expect_the_list()
        self.page.go_back()
        expect(self.page).to_have_url(self.before_url)

    def test_desktop_back_closes_the_note_and_forward_reopens_it(self):
        self._visit(self.list_url)
        self._open_note_from_the_list()

        self.page.go_back()

        expect(self.page).to_have_url(self.list_url)
        expect(self.title).to_be_hidden()
        self.page.go_forward()
        expect(self.page).to_have_url(self.note_url)
        expect(self.title).to_have_value("Groceries")
