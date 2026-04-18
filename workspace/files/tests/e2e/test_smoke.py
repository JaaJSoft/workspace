"""E2E smoke test: the authenticated files index renders."""
from __future__ import annotations

import re

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase


class FilesIndexSmokeTests(PlaywrightTestCase):
    """Loads /files as an authenticated user and checks the page title."""

    def test_files_index_renders(self):
        user = self.create_user(username="smoke")
        self.login_as(user)
        self.page.goto(f"{self.live_server_url}/files")
        expect(self.page).to_have_title(re.compile(r"Files", re.I))
