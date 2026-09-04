"""E2E cover for the note list's paging on a folder above the page size.

The JS unit tests pin the URL building and the merge logic; only a rendered
page can prove the first page is what the folder switch paints and that the
scroll sentinel really pulls the rest in. Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File

# NOTES_PAGE_SIZE in notes.js. 130 notes leave one partial page behind the
# first load, so the sentinel has to fire exactly once to reach the end.
PAGE_SIZE = 100
SEEDED = 130

ROWS = "button[data-note-uuid]"
SENTINEL = '[x-ref="notesSentinel"]'


class NoteListPaginationTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="note-pager")
        self.folder = File.objects.create(
            owner=self.user, name="Big", node_type=File.NodeType.FOLDER
        )
        # Strictly increasing timestamps: the list sorts on -updated_at, so
        # the expected order below is the reverse of the creation order.
        base = timezone.now() - timedelta(days=1)
        self.expected = []
        for i in range(1, SEEDED + 1):
            note = File.objects.create(
                owner=self.user,
                parent=self.folder,
                name=f"note-{i:03d}.md",
                node_type=File.NodeType.FILE,
                mime_type="text/markdown",
                type="markdown",
            )
            File.objects.filter(pk=note.pk).update(
                updated_at=base + timedelta(seconds=i)
            )
            self.expected.insert(0, str(note.uuid))

    def _open_folder(self):
        self.login_as(self.user)
        self.page.goto(
            f"{self.live_server_url}/notes?view=folder&folder={self.folder.uuid}"
        )
        expect(self.page.locator(ROWS).first).to_be_visible()
        # The debug toolbar overlays the pane and swallows pointer events.
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")

    def _rendered_uuids(self):
        return self.page.eval_on_selector_all(
            ROWS, "els => els.map(el => el.dataset.noteUuid)"
        )

    def test_the_folder_opens_on_one_page_and_scrolling_reaches_the_rest(self):
        self._open_folder()

        rows = self.page.locator(ROWS)
        expect(rows).to_have_count(PAGE_SIZE)
        self.assertEqual(self._rendered_uuids(), self.expected[:PAGE_SIZE])
        expect(self.page.locator(SENTINEL)).to_be_visible()

        # Scrolling the list to its bottom is the user gesture that brings
        # the sentinel into the observer's margin. Re-assert it every frame
        # until the page lands: a single assignment can run before the first
        # page has laid out and never move the scroller.
        self.page.wait_for_function(
            f"""() => {{
              if (document.querySelectorAll('{ROWS}').length > {PAGE_SIZE}) return true;
              const el = document.querySelector('{SENTINEL}').parentElement;
              el.scrollTop = el.scrollHeight;
              return false;
            }}"""
        )

        expect(rows).to_have_count(SEEDED)
        self.assertEqual(
            self._rendered_uuids(),
            self.expected,
            "the second page must follow the first with no gap or duplicate",
        )
        expect(self.page.locator(SENTINEL)).to_be_hidden()
