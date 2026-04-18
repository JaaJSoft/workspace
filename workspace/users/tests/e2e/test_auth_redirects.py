"""E2E test: unauthenticated access to protected pages redirects to /login.

Validates the full middleware chain: LoginRequiredMiddleware-like
behavior, Django's ``@login_required`` decorator on the target view,
and the ``?next=<quoted-path>`` query-string that carries the original
destination so post-login redirect works.
"""
from __future__ import annotations

import re

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase


class UnauthenticatedAccessTests(PlaywrightTestCase):
    """Hitting a protected URL anonymously lands on /login?next=..."""

    def test_files_redirects_anonymous_to_login_with_next(self):
        # No login_as — visit a protected URL anonymously.
        self.page.goto(f"{self.live_server_url}/files")
        # Browsers decode %2F back to /, so match on either form.
        expect(self.page).to_have_url(
            re.compile(r"/login\?next=(/|%2F)files"),
        )
