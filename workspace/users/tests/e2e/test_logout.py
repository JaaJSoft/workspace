"""E2E test: the logout flow clears the session and protected pages redirect.

The "Sign Out" button in the navbar user dropdown has an
``onclick="document.getElementById('logout-form').submit()"`` handler
pointing at a hidden POST form. We submit that form directly from the
test: it's the exact same side effect as clicking the button, but
without the CSS/dropdown-opening ceremony — simpler and deterministic.
"""
from __future__ import annotations

import re

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase


class LogoutFlowTests(PlaywrightTestCase):
    """Validates that logout clears the session."""

    def test_logout_clears_session_and_redirects(self):
        user = self.create_user(username="alice")
        self.login_as(user)
        self.page.goto(f"{self.live_server_url}/")

        # Baseline: we're authenticated — the dashboard loaded, not /login.
        expect(self.page).not_to_have_url(re.compile(r"/login"))

        # Submit the hidden logout form — same effect as clicking "Sign Out".
        self.page.locator("#logout-form").evaluate("f => f.submit()")
        self.page.wait_for_load_state("networkidle")

        # Revisiting a protected page now redirects to /login.
        self.page.goto(f"{self.live_server_url}/files")
        expect(self.page).to_have_url(re.compile(r"/login"))
