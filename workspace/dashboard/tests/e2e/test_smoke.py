"""E2E smoke test: the authenticated dashboard index renders."""
from __future__ import annotations

import re

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase


class DashboardIndexSmokeTests(PlaywrightTestCase):
    """Loads / as an authenticated user and checks the page title."""

    def test_dashboard_index_renders(self):
        user = self.create_user(username="smoke")
        self.login_as(user)
        self.page.goto(f"{self.live_server_url}/")
        expect(self.page).to_have_title(re.compile(r"Dashboard", re.I))
