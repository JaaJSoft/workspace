"""E2E: the dashboard draws each icon once on load, not the page twice.

Skipped unless E2E=1.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.common.tests.e2e.icons import IconScopeMixin


class DashboardIconScopeTests(IconScopeMixin, PlaywrightTestCase):
    def test_loading_the_dashboard(self):
        self.login_as(self.create_user(username="dashboard-icons"))
        self.page.goto(f"{self.live_server_url}/")

        apps = self.page.get_by_text("Choose which apps appear on your dashboard.")
        expect(
            apps.locator("xpath=..").locator("svg[data-lucide]").first
        ).to_be_attached()
        self.assert_bystander_kept()
