"""Alt+K opens the sidebar module switcher on any page inside a module.

The switcher is a focus-driven daisyUI dropdown, so the shortcut only has to
move focus to its trigger; Escape blurs it, which closes the panel again.
"""

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase

TRIGGER = "#module-switcher label"
PANEL = "#module-switcher .dropdown-content"
TILES = "#module-switcher-grid a"


class ModuleSwitcherShortcutTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.login_as(self.create_user(username="alice"))
        self.page.goto(f"{self.live_server_url}/files")
        self.page.wait_for_selector(TRIGGER)

    def test_alt_k_opens_the_switcher_and_escape_closes_it(self):
        expect(self.page.locator(PANEL)).to_be_hidden()

        self.page.keyboard.press("Alt+k")

        expect(self.page.locator(TRIGGER)).to_be_focused()
        expect(self.page.locator(PANEL)).to_be_visible()

        self.page.keyboard.press("Escape")

        expect(self.page.locator(PANEL)).to_be_hidden()

    def test_arrows_and_letters_move_across_the_tiles(self):
        self.page.keyboard.press("Alt+k")
        expect(self.page.locator(PANEL)).to_be_visible()

        self.page.keyboard.press("ArrowDown")
        expect(self.page.locator(TILES).first).to_be_focused()

        self.page.keyboard.press("ArrowRight")
        expect(self.page.locator(TILES).nth(1)).to_be_focused()

        self.page.keyboard.press("c")
        focused = self.page.evaluate("document.activeElement.textContent.trim()")
        self.assertTrue(focused.startswith("C"), focused)

    def test_the_panel_escapes_the_collapsed_rail(self):
        self.page.get_by_role("button", name="Collapse sidebar").click()
        self.page.keyboard.press("Alt+k")
        expect(self.page.locator(PANEL)).to_be_visible()

        # A tile past the 4rem rail must be hit-testable, i.e. not clipped by
        # the drawer.
        last = self.page.locator(TILES).last
        expect(last).to_be_in_viewport()
        box = last.bounding_box()
        rail = self.page.locator("aside").first.bounding_box()
        self.assertGreater(box["x"], rail["x"] + rail["width"])
        hit = self.page.evaluate(
            "([x, y]) => document.elementFromPoint(x, y)?.closest('a')?.textContent.trim()",
            [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2],
        )
        self.assertEqual(hit, last.text_content().strip())
