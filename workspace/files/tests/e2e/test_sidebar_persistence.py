"""E2E test: the files sidebar collapsed state survives a page reload.

The files module persists the sidebar collapse state in
``localStorage['sidebarCollapsed']`` (see ``sidebarCollapse()`` in
``files/ui/static/files/ui/js/file_browser.js``). A backend test cannot
reach this code path — it lives entirely in the browser, with the
write happening in ``toggleCollapse()`` and the read happening in the
component's initial ``x-data`` binding when the page mounts.

The bug class this guards against: someone migrates the persistence
to a ``UserSetting`` row, or to a different ``localStorage`` key, and
forgets to read it back on init — F5 silently reverts the sidebar to
its default expanded state. Same shape as the "F5 reverts my setting"
class documented in CLAUDE.md, but for a browser-local store.
"""
from __future__ import annotations

import re

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase


def re_w16():
    """Tailwind class signaling a collapsed sidebar (``w-16`` vs ``w-72``)."""
    return re.compile(r"\bw-16\b")


class FilesSidebarPersistenceTests(PlaywrightTestCase):
    """Toggling the sidebar via the UI persists across a page reload."""

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

        # Baseline: a fresh ``BrowserContext`` has an empty
        # ``localStorage`` so ``sidebarCollapse()`` falls back to
        # expanded (``w-72``).
        expect(aside).not_to_have_class(re_w16())

        # Collapse via the actual button — same code path as a user click.
        self.page.get_by_role("button", name="Collapse sidebar").click()
        expect(aside).to_have_class(re_w16())

        # Sanity: the persistence write actually happened on the JS
        # side. If this assertion ever fails, it means ``toggleCollapse``
        # was refactored away from ``localStorage`` and the rest of
        # the test would be measuring nothing.
        stored = self.page.evaluate(
            "() => localStorage.getItem('sidebarCollapsed')"
        )
        assert stored == "true", (
            f"expected localStorage['sidebarCollapsed'] = 'true' after click, "
            f"got {stored!r}"
        )

        # F5 — Alpine re-initializes ``sidebarCollapse()`` from scratch.
        # If the initial binding still reads ``localStorage``, the
        # sidebar mounts collapsed.
        self.page.reload()

        # Re-resolve the locator: ``page.reload()`` invalidates the
        # previous element handle. ``aside`` is a Locator, so it
        # auto-relocates — but we re-query for clarity.
        aside = self.page.locator("aside").first
        expect(aside).to_have_class(re_w16())
