"""Alt+M opens the navbar module switcher from anywhere inside a module.

The switcher is a focus-driven daisyUI dropdown, so the shortcut only has to
move focus to its trigger; Escape blurs it, which closes the panel again.
"""

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase

TRIGGER = "#module-switcher label"
PANEL = "#module-switcher .dropdown-content"


class ModuleSwitcherShortcutTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.login_as(self.create_user(username="alice"))
        self.page.goto(f"{self.live_server_url}/files")
        self.page.wait_for_selector(TRIGGER)

    def test_alt_m_opens_the_switcher_and_escape_closes_it(self):
        expect(self.page.locator(PANEL)).to_be_hidden()

        self.page.keyboard.press("Alt+m")

        expect(self.page.locator(TRIGGER)).to_be_focused()
        expect(self.page.locator(PANEL)).to_be_visible()

        self.page.keyboard.press("Escape")

        expect(self.page.locator(PANEL)).to_be_hidden()

    def test_alt_m_does_nothing_on_the_home_page(self):
        self.page.goto(f"{self.live_server_url}/")
        self.page.wait_for_selector("#navbar-brand")

        self.page.keyboard.press("Alt+m")

        self.assertEqual(self.page.locator("#module-switcher").count(), 0)
