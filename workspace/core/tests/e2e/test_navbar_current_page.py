"""The user menu's entry for the page already open.

It is rendered inert. `pointer-events: none` alone would not do it: that stops
pointer hit testing and nothing else, so a screen reader or a script could
still activate the link and reload the page underneath the open menu.
"""

from workspace.common.tests.e2e.base import PlaywrightTestCase

ENTRY = "ul.dropdown-content a:has-text('Settings')"


class CurrentPageMenuEntryTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.login_as(self.create_user(username="alice"))

    def _entry_on(self, path):
        self.page.goto(f"{self.live_server_url}{path}")
        self.page.wait_for_selector(".navbar")
        return self.page.locator(ENTRY).first

    def _survives_a_click(self, entry):
        """Whether the document is still the one that was loaded.

        A stamp on the window, not the URL: following a link to the page you
        are already on leaves the URL untouched and reloads everything, so the
        two cases are indistinguishable from the address alone.
        """
        self.page.evaluate("window.__sameDocument = true")
        entry.evaluate("el => el.click()")
        self.page.wait_for_timeout(800)
        return self.page.evaluate("window.__sameDocument === true")

    def test_the_entry_for_the_current_page_cannot_be_activated(self):
        entry = self._entry_on("/users/settings")
        self.assertIsNone(entry.get_attribute("href"))
        self.assertEqual(entry.get_attribute("aria-current"), "page")
        self.assertTrue(self._survives_a_click(entry))

    def test_the_same_entry_is_a_working_link_from_elsewhere(self):
        """Also what makes the assertion above mean anything: the same probe
        has to come back the other way when the entry is a real link."""
        entry = self._entry_on("/")
        self.assertEqual(entry.get_attribute("href"), "/users/settings")
        self.assertIsNone(entry.get_attribute("aria-current"))
        self.assertFalse(self._survives_a_click(entry))
        self.assertTrue(self.page.url.endswith("/users/settings"))
