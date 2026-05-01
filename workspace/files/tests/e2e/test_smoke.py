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

        # Anti-regression guard, scoped to the two endpoints this test
        # actually cares about. Before this commit, both URLs 404'd for any
        # fresh user because the JS fetched them on init; the JS swallowed
        # the failure but the browser still logged it as a console error.
        # The base test case has STRICT_NO_JS_ERRORS=False (opt-in for an
        # unrelated navbar race), so the regression was invisible until we
        # watched the responses directly.
        #
        # The watcher is intentionally narrow rather than "any 4xx on a
        # same-origin URL": a generic guard belongs in the base harness
        # once the navbar race is fixed and STRICT_NO_JS_ERRORS can flip
        # to True. Until then, scoping here to the documented offenders
        # keeps the smoke test from flaking on unrelated app-shell 4xx
        # (e.g. ``/api/v1/users/<id>/avatar``, which 404s by design and
        # is handled by an ``onerror`` placeholder in the navbar).
        watched_paths = (
            "/api/v1/settings/files/preferences",
            "/api/v1/settings/files/viewer",
        )
        bad_responses: list[str] = []

        def _watch(r):
            if r.status < 400 or self.live_server_url not in r.url:
                return
            if not any(p in r.url for p in watched_paths):
                return
            bad_responses.append(f"{r.status} {r.request.method} {r.url}")

        self.page.on("response", _watch)

        self.page.goto(f"{self.live_server_url}/files")
        expect(self.page).to_have_title(re.compile(r"Files", re.I))
        # Let deferred init / Alpine post-mount fetches settle so any 4xx
        # they emit is observed before we assert.
        self.page.wait_for_timeout(500)

        assert not bad_responses, (
            "Watched files-prefs endpoints returned 4xx/5xx during /files "
            "load (regression: see commit history for context):\n  "
            + "\n  ".join(bad_responses)
        )
