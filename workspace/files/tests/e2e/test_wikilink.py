"""E2E tests for the [[ note-autocomplete in the markdown viewer.

Split into two classes:
  * WikilinkHelperTests - asserts the pure trigger helpers in a real browser
    with NO third-party network (always runs under E2E=1).
  * WikilinkEditorTests - drives the real Crepe editor from the vendored
    local bundle (no CDN dependency).
"""

from __future__ import annotations

from django.core.files.base import ContentFile
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.services import FileService


# Hardcoded because StaticLiveServerTestCase serves original (unhashed) static
# paths through the staticfiles finders, regardless of production storage.
HELPER_MODULE_URL = "/static/files/ui/js/wikilink_match.js"


class WikilinkHelperTests(PlaywrightTestCase):
    def test_match_trigger_and_range(self):
        # Any same-origin document that serves static works; /login needs no auth.
        self.page.goto(f"{self.live_server_url}/login")
        result = self.page.evaluate(
            """async ([base, modPath]) => {
                const m = await import(base + modPath);
                return {
                    typed: m.matchTrigger('see [[meet'),
                    closed: m.matchTrigger('[[done]]'),
                    none: m.matchTrigger('no trigger here'),
                    empty: m.matchTrigger('start [['),
                    range: m.replacementRange(10, 6),
                };
            }""",
            [self.live_server_url, HELPER_MODULE_URL],
        )
        self.assertEqual(result["typed"], {"query": "meet", "length": 6})
        self.assertIsNone(result["closed"])
        self.assertIsNone(result["none"])
        self.assertEqual(result["empty"], {"query": "", "length": 2})
        self.assertEqual(result["range"], {"from": 4, "to": 10})


class WikilinkEditorTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="wikilink")
        self.login_as(self.user)
        self.alpha = FileService.create_file(
            self.user,
            "Alpha.md",
            content=ContentFile(b"# Alpha\n\nStart typing here.\n", name="Alpha.md"),
            mime_type="text/markdown",
        )
        self.beta = FileService.create_file(
            self.user,
            "Beta Target.md",
            # Markdown-shaped body so the content sniffer labels it 'markdown'
            # (a bare "# Beta" is too short and detects as plain text, which the
            # ?type=markdown search filter would then exclude).
            content=ContentFile(
                b"# Beta Target\n\nNotes about the beta milestone.\n",
                name="Beta Target.md",
            ),
            mime_type="text/markdown",
        )

    def _open_editor(self):
        # The notes app auto-opens the note named by ?file=, mounting the viewer.
        self.page.goto(f"{self.live_server_url}/notes?file={self.alpha.uuid}")
        editor = self.page.locator(".ProseMirror").first
        expect(editor).to_be_visible(timeout=20000)
        return editor

    def test_menu_hidden_before_trigger(self):
        # Regression: on open, before any "[[" is typed, the popup must stay
        # hidden. SlashProvider only writes data-show="false" on its first
        # editor update, so without an initial hidden state the empty menu
        # flashed at the top-left until the user started typing.
        self._open_editor()
        menu = self.page.locator('[data-testid="wikilink-menu"]')
        expect(menu).to_be_hidden(timeout=5000)

    def test_double_bracket_opens_menu(self):
        editor = self._open_editor()
        editor.click()
        self.page.keyboard.press("Control+End")
        self.page.keyboard.type("[[")
        menu = self.page.locator('[data-testid="wikilink-menu"]')
        expect(menu).to_be_visible(timeout=5000)

    def test_typing_query_lists_matching_note(self):
        editor = self._open_editor()
        editor.click()
        self.page.keyboard.press("Control+End")
        self.page.keyboard.type("[[beta")
        item = self.page.locator(
            '[data-testid="wikilink-item"]', has_text="Beta Target"
        )
        expect(item).to_be_visible(timeout=5000)

    def test_picking_note_inserts_link(self):
        editor = self._open_editor()
        editor.click()
        self.page.keyboard.press("Control+End")
        self.page.keyboard.type("[[beta")
        item = self.page.locator(
            '[data-testid="wikilink-item"]', has_text="Beta Target"
        )
        expect(item).to_be_visible(timeout=5000)
        item.click()
        link = self.page.locator('.ProseMirror a[href*="' + str(self.beta.uuid) + '"]')
        expect(link).to_be_visible(timeout=5000)
        expect(link).to_have_text("Beta Target")
        # The literal "[[beta" trigger text must be gone.
        expect(self.page.locator(".ProseMirror")).not_to_contain_text("[[beta")

    def test_enter_picks_highlighted_and_escape_closes(self):
        editor = self._open_editor()
        editor.click()
        self.page.keyboard.press("Control+End")

        # Empty "[[" lists recent notes (both created notes qualify).
        self.page.keyboard.type("[[")
        expect(self.page.locator('[data-testid="wikilink-menu"]')).to_be_visible(
            timeout=5000
        )

        # Esc closes the menu and leaves the text alone.
        self.page.keyboard.press("Escape")
        expect(self.page.locator('[data-testid="wikilink-menu"]')).to_be_hidden(
            timeout=3000
        )

        # Re-open, narrow to Beta, Enter inserts the link.
        self.page.keyboard.type("beta")
        expect(
            self.page.locator('[data-testid="wikilink-item"]', has_text="Beta Target")
        ).to_be_visible(timeout=5000)
        self.page.keyboard.press("Enter")
        expect(
            self.page.locator('.ProseMirror a[href*="' + str(self.beta.uuid) + '"]')
        ).to_be_visible(timeout=5000)

    def test_search_error_clears_stale_results(self):
        editor = self._open_editor()
        editor.click()
        self.page.keyboard.press("Control+End")
        # Populate the menu with a real result first.
        self.page.keyboard.type("[[beta")
        expect(
            self.page.locator('[data-testid="wikilink-item"]', has_text="Beta Target")
        ).to_be_visible(timeout=5000)
        # Force the search endpoint to fail, then trigger a fresh query.
        self.context.route(
            "**/api/v1/files**",
            lambda route: route.fulfill(status=500, body=""),
        )
        self.page.keyboard.type("x")  # "[[betax" -> failing search
        expect(self.page.locator(".wikilink-error")).to_be_visible(timeout=5000)
        # Arrow keys must not resurrect the stale list over the error.
        self.page.keyboard.press("ArrowDown")
        expect(self.page.locator('[data-testid="wikilink-item"]')).to_have_count(0)
