"""E2E: a file inside a shared folder renders in its viewer, read-only.

Three bugs hid here, and none of them is visible to a Django test because
the page's HTML was correct every time: the viewer's ``initEditor()``
threw on a global only ``base.html`` loads, so the editor never mounted;
the viewer sized itself with ``h-full`` over a flex chain that resolves to
nothing on a card, so it mounted into a zero-height box; and Milkdown's
stylesheet was loaded by the two authenticated pages rather than by the
viewer, so on the share page its toolbar, slash menu and block handle lost
the rules that hide them and rendered as a column of edit controls under
the text. Only a browser sees a mounted editor, a box with a height, and
a control that is actually hidden.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase

from ._share_fixtures import README_TEXT, build_shared_tree

# Every widget Crepe mounts next to the editor. On a page nobody can edit,
# none of them may be on screen.
EDIT_CHROME = ".milkdown-toolbar, .milkdown-slash-menu, .milkdown-block-handle"


class SharedViewerTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        build_shared_tree(self)

    def test_markdown_inside_a_shared_folder_renders_without_edit_controls(self):
        self.page.goto(
            f"{self.live_server_url}/files/shared/{self.link.token}"
            f"?node={self.readme.uuid}"
        )

        editor = self.page.locator(".milkdown .ProseMirror")
        expect(editor).to_be_visible()
        expect(editor).to_contain_text(README_TEXT)

        # A zero-height mount is "visible" to Playwright as long as it has
        # overflow; the height is the thing bug 2 took away.
        box = editor.bounding_box()
        self.assertIsNotNone(box)
        self.assertGreater(box["height"], 40)

        # Crepe hides some of its widgets with display:none and others with
        # opacity:0, so Playwright's own hidden check is not the right
        # question. Without the stylesheet they were all in flow, opaque,
        # under the text: out of flow and imperceptible is what pins that.
        controls = self.page.evaluate(
            """(selector) => [...document.querySelectorAll(selector)].map((el) => {
              const cs = getComputedStyle(el);
              return {
                name: el.className,
                outOfFlow: cs.position === 'absolute' || cs.position === 'fixed',
                imperceptible: cs.display === 'none' || cs.opacity === '0',
              };
            })""",
            EDIT_CHROME,
        )
        self.assertTrue(controls, "Crepe mounted none of its widgets")
        for control in controls:
            self.assertTrue(control["outOfFlow"], control["name"])
            self.assertTrue(control["imperceptible"], control["name"])
