"""E2E test: the dashboard preferences panel stays open while its controls are used."""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase


class DashboardPreferencesPopoverTests(PlaywrightTestCase):
    def _open_panel(self):
        user = self.create_user(username="prefs")
        self.login_as(user)
        self.page.goto(f"{self.live_server_url}/")
        self.page.get_by_role("button", name="Dashboard preferences").click()
        panel = self.page.locator("#dashboard-preferences-panel")
        expect(panel).to_be_visible()
        return panel

    def test_temperature_select_keeps_the_panel_open(self):
        # Opening Chromium's base-select picker leaves document.activeElement on
        # <body>, so a panel shown on :focus-within fades out about 200ms later:
        # the wait has to outlast that fade or the assertion passes before it.
        panel = self._open_panel()
        unit = panel.locator("select[x-model='temperatureUnit']")

        unit.click()
        self.page.wait_for_timeout(500)
        expect(panel).to_be_visible()

        unit.get_by_role("option", name="°F").click()
        self.page.wait_for_timeout(500)
        expect(panel).to_be_visible()
        expect(unit).to_have_value("fahrenheit")

    def test_outside_click_and_escape_close_the_panel(self):
        panel = self._open_panel()
        self.page.mouse.click(5, 5)
        expect(panel).to_be_hidden()

        self.page.get_by_role("button", name="Dashboard preferences").click()
        expect(panel).to_be_visible()
        self.page.keyboard.press("Escape")
        expect(panel).to_be_hidden()
