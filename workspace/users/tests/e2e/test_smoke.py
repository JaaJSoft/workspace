"""E2E smoke test: the authenticated user settings page renders."""
from __future__ import annotations

import re

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase


class UserSettingsSmokeTests(PlaywrightTestCase):
    """Loads /users/settings as an authenticated user and checks the title."""

    def test_settings_page_renders(self):
        user = self.create_user(username="smoke")
        self.login_as(user)
        self.page.goto(f"{self.live_server_url}/users/settings")
        expect(self.page).to_have_title(re.compile(r"Settings", re.I))
