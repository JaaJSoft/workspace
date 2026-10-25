"""E2E: picking a language in the settings reloads the page in it.

The switch has two halves the unit tests cannot join: the server-rendered
strings come from the stored setting at the next request, and the gettext()
globals from the JavaScript catalog the reloaded page links.
"""

from __future__ import annotations

from django.core.cache import cache
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.users.services.settings import get_setting


class SettingsLanguageTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/users/settings#region")
        self.page.wait_for_load_state("networkidle")

    def tearDown(self):
        super().tearDown()
        cache.clear()

    def test_choosing_french_reloads_the_interface_in_french(self):
        expect(self.page.locator("h1")).to_have_text("Settings")

        with self.page.expect_navigation():
            self.page.get_by_label("Language").select_option("fr")
        self.page.wait_for_load_state("networkidle")

        expect(self.page.locator("h1")).to_have_text("Paramètres")
        expect(self.page.locator("html")).to_have_attribute("lang", "fr")
        self.assertEqual(self.page.evaluate("gettext('Today')"), "Aujourd’hui")
        # The tab survives the reload: the user lands where they made the choice.
        expect(self.page.get_by_role("heading", name="Fuseau horaire")).to_be_visible()
        self.assertEqual(get_setting(self.user, "core", "language"), "fr")
