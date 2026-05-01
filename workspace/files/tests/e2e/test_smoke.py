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

        # Capture every same-origin 4xx/5xx response. The base test case has
        # STRICT_NO_JS_ERRORS=False (opt-in for an unrelated navbar race),
        # so a redundant fetch that 404s for a fresh user (e.g. an early
        # /api/v1/settings/files/* call when the row doesn't exist yet)
        # would silently land in the browser console without failing the
        # test. We watch responses directly to catch that class of bug.
        #
        # ``/api/v1/users/<id>/avatar`` is allowed to 404: when a user has
        # no uploaded avatar the navbar's ``<img>`` tag relies on the
        # browser's ``onerror`` to swap in a letter placeholder
        # (see common/templates/ui/partials/_user_avatar_inner.html).
        # That's an intentional codebase-wide pattern, not a files-module
        # bug, so the guard ignores it.
        bad_responses: list[str] = []

        def _watch(r):
            if r.status < 400 or self.live_server_url not in r.url:
                return
            if re.search(r"/api/v1/users/\d+/avatar", r.url):
                return
            bad_responses.append(f"{r.status} {r.request.method} {r.url}")

        self.page.on("response", _watch)

        self.page.goto(f"{self.live_server_url}/files")
        expect(self.page).to_have_title(re.compile(r"Files", re.I))
        # Let deferred init / Alpine post-mount fetches settle so any 4xx
        # they emit is observed before we assert.
        self.page.wait_for_timeout(500)

        assert not bad_responses, (
            "Same-origin requests returned 4xx/5xx during /files load:\n  "
            + "\n  ".join(bad_responses)
        )
