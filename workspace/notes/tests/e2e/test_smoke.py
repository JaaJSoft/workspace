"""E2E smoke test: the authenticated notes index renders."""
from __future__ import annotations

import re

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase


class NotesIndexSmokeTests(PlaywrightTestCase):
    """Loads /notes as an authenticated user and checks the page title."""

    def test_notes_index_renders(self):
        user = self.create_user(username="smoke")
        self.login_as(user)
        self.page.goto(f"{self.live_server_url}/notes")
        expect(self.page).to_have_title(re.compile(r"Notes", re.I))
