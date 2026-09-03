"""E2E test: the files sidebar collapsed state survives a page reload.

The preference is a per-module user setting (``files`` / ``sidebar_collapsed``).
``toggleCollapse()`` writes it through the settings API, the server renders the
next page with the sidebar already at that width, and ``sidebarCollapse()``
seeds its state from the value the shell embeds. A backend test cannot reach
the browser half of that loop - the write happens in ``toggleCollapse()`` and
the read in the component's initial ``x-data`` binding when the page mounts.

The bug class this guards against: the write moves to another key, or the
component stops reading the embedded value back on mount - F5 silently reverts
the sidebar to its default expanded state. Same shape as the "F5 reverts my
setting" class documented in CLAUDE.md.
"""

from __future__ import annotations

import re

from django.core.cache import cache
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.core.setting_keys import SIDEBAR_COLLAPSED
from workspace.users.services.settings import get_setting


def re_expanded():
    """Tailwind class opening the sidebar on desktop (the ``w-16`` rail is always there)."""
    return re.compile(r"\blg:w-72\b")


class FilesSidebarPersistenceTests(PlaywrightTestCase):
    """Toggling the sidebar via the UI persists across a page reload."""

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def test_collapsed_state_survives_reload(self):
        user = self.create_user(username="alice")
        self.login_as(user)

        # ``sidebarCollapse()`` forces ``collapsed = true`` when the
        # viewport is below the ``lg`` breakpoint (1024 px), which would
        # mask any persistence bug. Playwright's default viewport is
        # 1280×720 so we're already in desktop mode, but pin it
        # explicitly so the test doesn't silently flip into "mobile
        # always collapsed" mode if the default ever changes.
        self.page.set_viewport_size({"width": 1280, "height": 800})
        self.page.goto(f"{self.live_server_url}/files")

        aside = self.page.locator("aside").first

        # Baseline: no setting stored, so the server renders the sidebar
        # expanded and the component agrees.
        expect(aside).to_have_class(re_expanded())

        # Collapse via the actual button - same code path as a user click.
        # The write is fire-and-forget, so wait for the request to land
        # before reading the setting back.
        with self.page.expect_response("**/api/v1/settings/files/sidebar_collapsed"):
            self.page.get_by_role("button", name="Collapse sidebar").click()
        expect(aside).not_to_have_class(re_expanded())

        # Sanity: the persistence write actually happened. If this ever
        # fails, ``toggleCollapse`` was refactored away from the settings
        # API and the rest of the test would be measuring nothing.
        self.assertIs(get_setting(user, "files", SIDEBAR_COLLAPSED), True)

        # F5 - the server renders the collapsed width and Alpine
        # re-initializes ``sidebarCollapse()`` from the embedded value.
        self.page.reload()

        # Re-resolve the locator: ``page.reload()`` invalidates the
        # previous element handle. ``aside`` is a Locator, so it
        # auto-relocates - but we re-query for clarity.
        aside = self.page.locator("aside").first
        expect(aside).not_to_have_class(re_expanded())
