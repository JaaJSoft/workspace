"""End-to-end test for the chat sidebar's initial state on mobile.

Regression: a desktop session leaves the sidebar expanded. When the same
account then loads ``/chat`` at a mobile viewport, the very first render used
that preference (``collapsed=false``, the expanded width) and only switched to
the ``w-16`` rail after ``init()`` ran. The user saw a visible "expanded ->
collapsed" flicker.

The fix seeds ``collapsed`` synchronously in the chatApp factory, taking
``window.matchMedia('(max-width: 1023px)')`` into account, and the server
renders the expanded width behind a ``lg:`` variant, so a mobile viewport never
paints anything wider than the rail - before Alpine binds or after.

Skipped unless ``E2E=1`` is set.
"""

from __future__ import annotations

import re

from django.core.cache import cache
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.core.setting_keys import SIDEBAR_COLLAPSED
from workspace.users.services.settings import set_setting

MOBILE_VIEWPORT = {"width": 375, "height": 667}
RAIL_WIDTH = 64


class ChatMobileSidebarTests(PlaywrightTestCase):
    """Pins down the sidebar's initial collapsed state on mobile."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="viewer", password="pass12345")
        # An explicit "expanded" desktop preference, then a sampler that
        # records every distinct width the <aside> is painted at, installed
        # before any document script runs. Catching a transient expanded
        # paint requires observing the very first frames, which happen
        # before DOMContentLoaded (Alpine is loaded via `defer`).
        set_setting(self.user, "chat", SIDEBAR_COLLAPSED, False)
        self.context.add_init_script(
            """
            window.__asideWidths = [];
            (function sample() {
              const aside = document.querySelector('aside');
              if (aside) {
                const width = Math.round(aside.getBoundingClientRect().width);
                const widths = window.__asideWidths;
                if (widths[widths.length - 1] !== width) widths.push(width);
              }
              if (performance.now() < 5000) requestAnimationFrame(sample);
            })();
            """
        )

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def test_mobile_load_never_renders_expanded_sidebar(self):
        self.page.set_viewport_size(MOBILE_VIEWPORT)
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/chat")

        # Wait until Alpine has fully booted: the aside must end up in the
        # collapsed (w-16) state on mobile.
        aside = self.page.locator("aside").first
        expect(aside).to_have_class(re.compile(r"\bw-16\b"))
        self.page.wait_for_timeout(500)

        # Any width above the rail's proves the sidebar rendered as
        # expanded at some point - the "expanded -> collapsed" flicker.
        widths = self.page.evaluate("window.__asideWidths")
        assert widths == [RAIL_WIDTH], (
            "sidebar painted wider than the rail on mobile during load - "
            f"flicker regression. Widths seen: {widths}"
        )
