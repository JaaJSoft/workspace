"""E2E: the arrow keys and the overlay arrows walk the files of a shared folder.

The page renders previous and next as real links; a window keydown
handler clicks them unless the visitor is typing or has the editor
focused. Which key lands where, and whether a focused editor keeps its
caret instead of changing file, are browser facts.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase

from ._share_fixtures import build_shared_tree


class SharedArrowNavigationTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        build_shared_tree(self)

    def _open(self, node):
        self.page.goto(
            f"{self.live_server_url}/files/shared/{self.link.token}?node={node.uuid}"
        )
        expect(self.page.locator(".milkdown .ProseMirror")).to_be_visible()

    def test_keys_and_overlay_arrows_move_between_neighbours(self):
        self._open(self.readme)
        # First of two: only a next arrow, and the counter says so.
        expect(self.page.get_by_role("link", name="Next file")).to_be_visible()
        expect(self.page.get_by_role("link", name="Previous file")).to_have_count(0)
        expect(self.page.get_by_text("1 / 2")).to_be_visible()

        self.page.keyboard.press("ArrowRight")
        self.page.wait_for_url(lambda url: f"node={self.notes.uuid}" in url)
        expect(self.page.get_by_text("2 / 2")).to_be_visible()
        expect(self.page.get_by_role("link", name="Next file")).to_have_count(0)

        # At the end, the key does nothing.
        self.page.keyboard.press("ArrowRight")
        self.page.wait_for_timeout(300)
        self.assertIn(f"node={self.notes.uuid}", self.page.url)

        self.page.keyboard.press("ArrowLeft")
        self.page.wait_for_url(lambda url: f"node={self.readme.uuid}" in url)

        self.page.get_by_role("link", name="Next file").click()
        self.page.wait_for_url(lambda url: f"node={self.notes.uuid}" in url)
        self.page.get_by_role("link", name="Previous file").click()
        self.page.wait_for_url(lambda url: f"node={self.readme.uuid}" in url)

    def test_keys_stay_with_a_viewer_that_holds_the_focus(self):
        """The read-only markdown editor never takes focus, so arrows walk
        away from it. Monaco does: its input is a textarea that moves a
        caret with the arrows, and that must keep them."""
        from django.core.files.base import ContentFile

        from workspace.files.models import File

        plain = File.objects.create(
            owner=self.owner,
            name="zzz-plain.txt",
            node_type=File.NodeType.FILE,
            parent=self.sub,
            type="text",
            category="text",
            mime_type="text/plain",
        )
        plain.content = ContentFile(b"plain text\n", name="zzz-plain.txt")
        plain.size = 11
        plain.save()

        self.page.goto(
            f"{self.live_server_url}/files/shared/{self.link.token}?node={plain.uuid}"
        )
        editor = self.page.locator(".monaco-editor")
        expect(editor).to_be_visible()
        # Last of three: a previous arrow exists, so ArrowLeft would move.
        expect(self.page.get_by_role("link", name="Previous file")).to_be_visible()

        editor.click()
        self.assertEqual(
            self.page.evaluate("document.activeElement.tagName"), "TEXTAREA"
        )
        self.page.keyboard.press("ArrowLeft")
        self.page.wait_for_timeout(300)
        self.assertIn(f"node={plain.uuid}", self.page.url)

    def test_a_file_shared_on_its_own_has_no_arrows(self):
        self.page.goto(f"{self.live_server_url}/files/shared/{self.link.token}")
        self.page.get_by_role("link", name="Sub").click()
        self.page.get_by_role("link", name="readme.md").click()
        self.page.wait_for_url(lambda url: f"node={self.readme.uuid}" in url)
        # Inside the folder there is a neighbour...
        expect(self.page.get_by_role("link", name="Next file")).to_be_visible()

        from workspace.files.models import FileShareLink

        alone = FileShareLink.objects.create(
            file=self.readme, created_by=self.owner, mode=FileShareLink.Mode.READ
        )
        self.page.goto(f"{self.live_server_url}/files/shared/{alone.token}")
        expect(self.page.locator(".milkdown .ProseMirror")).to_be_visible()
        # ...but the same file shared on its own has nothing to walk.
        expect(self.page.get_by_role("link", name="Next file")).to_have_count(0)
        expect(self.page.get_by_role("link", name="Previous file")).to_have_count(0)
        self.page.keyboard.press("ArrowRight")
        self.page.wait_for_timeout(300)
        self.assertNotIn("node=", self.page.url)
