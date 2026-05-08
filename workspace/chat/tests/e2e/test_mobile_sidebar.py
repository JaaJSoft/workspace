"""End-to-end test for the chat sidebar's initial state on mobile.

Regression: a desktop session leaves ``localStorage.chatSidebarCollapsed``
set to ``"false"`` (sidebar expanded). When the same browser then loads
``/chat`` at a mobile viewport, the very first render used the
localStorage value (``collapsed=false`` → ``w-80``) and only switched to
``w-16`` after ``init()`` ran. The user saw a visible "expanded → collapsed"
flicker. The fix initializes ``collapsed`` synchronously in the chatApp
factory, taking ``window.matchMedia('(max-width: 1023px)')`` into account,
so Alpine's first binding pass paints ``w-16`` directly with no transition.

Skipped unless ``E2E=1`` is set.
"""
from __future__ import annotations

import re

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase


MOBILE_VIEWPORT = {"width": 375, "height": 667}


class ChatMobileSidebarTests(PlaywrightTestCase):
    """Pins down the sidebar's initial collapsed state on mobile."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="viewer", password="pass12345")
        # Seed an "expanded" desktop preference and install a MutationObserver
        # that records every distinct className the <aside> ever wears,
        # before any document script runs. Catching a transient w-80 → w-16
        # transition requires observing the very first bind, which happens
        # before DOMContentLoaded (Alpine is loaded via `defer`).
        self.context.add_init_script(
            """
            try { localStorage.setItem('chatSidebarCollapsed', 'false'); } catch (_) {}
            window.__asideClassLog = [];
            const recordIfNew = () => {
              const aside = document.querySelector('aside');
              if (!aside) return;
              const cls = aside.className;
              const log = window.__asideClassLog;
              if (log.length === 0 || log[log.length - 1] !== cls) {
                log.push(cls);
              }
            };
            new MutationObserver(recordIfNew).observe(
              document.documentElement,
              { childList: true, subtree: true,
                attributes: true, attributeFilter: ['class'] }
            );
            """
        )

    def test_mobile_load_never_renders_expanded_sidebar(self):
        self.page.set_viewport_size(MOBILE_VIEWPORT)
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/chat")

        # Wait until Alpine has fully booted: the aside must end up in the
        # collapsed (w-16) state on mobile.
        aside = self.page.locator("aside").first
        expect(aside).to_have_class(re.compile(r"\bw-16\b"))

        # Now inspect the recorded class history. Any snapshot containing
        # `w-80` proves the sidebar rendered as expanded at some point —
        # i.e. the "expanded → collapsed" flicker the user reported.
        history = self.page.evaluate("window.__asideClassLog || []")
        expanded_snapshots = [c for c in history if "w-80" in c.split()]
        assert not expanded_snapshots, (
            "sidebar rendered expanded (w-80) on mobile during load — "
            "flicker regression. Class history:\n  "
            + "\n  ".join(history)
        )
