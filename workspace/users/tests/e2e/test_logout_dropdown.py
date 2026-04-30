"""E2E test: logout via the actual "Sign Out" item in the navbar dropdown.

The companion test ``test_logout.py`` shortcuts the UI by submitting
``#logout-form`` directly via JS — that proves the *form* works but
skips everything between the user and the form: the dropdown opening,
the menu item being hit-testable, and the inline ``onclick`` handler
that wires the click to ``form.submit()``.

This test exercises the real click path. It catches the bug class:

  * the inline ``onclick`` is dropped or renamed (the form never submits
    despite a successful click),
  * the dropdown content is masked by another element (daisyUI
    ``dropdown-content`` ships with ``z-[1]`` — easy to be hidden
    behind a header / sidebar overlay with a higher stacking context),
  * the trigger ``<label tabindex="0">`` loses focus before the click
    bubbles, e.g. when migrating from the focus-driven daisyUI
    dropdown to a JS-driven one without preserving the click target.
"""
from __future__ import annotations

import re

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase


class LogoutDropdownClickTests(PlaywrightTestCase):
    """The "Sign Out" item in the navbar user dropdown logs the user out."""

    def test_signout_via_navbar_dropdown(self):
        user = self.create_user(username="alice")
        self.login_as(user)
        self.page.goto(f"{self.live_server_url}/")

        # Baseline: we're on an authenticated page (LOGIN_REDIRECT_URL = '/').
        expect(self.page).not_to_have_url(re.compile(r"/login"))

        # Open the user menu. The trigger is the avatar wrapped in a
        # ``<label tabindex="0">`` inside the only ``.dropdown-end`` on
        # the page — clicking it focuses the label, and daisyUI's CSS
        # opens ``dropdown-content`` via ``:focus-within``. We don't
        # care which mechanism opens it; we care that the menu becomes
        # hit-testable.
        trigger = self.page.locator(".dropdown.dropdown-end > label[tabindex='0']")
        expect(trigger).to_be_visible()
        trigger.click()

        # The "Sign Out" button is the last item in the dropdown menu.
        # ``get_by_role("button", name="Sign Out")`` matches the visible
        # accessible name regardless of the surrounding markup. If the
        # dropdown failed to open or another element masks it (z-index
        # regression), this assertion fails before we even click.
        sign_out = self.page.get_by_role("button", name="Sign Out", exact=True)
        expect(sign_out).to_be_visible()

        # Click the real button — exercises the inline
        # ``onclick="document.getElementById('logout-form').submit()"``.
        # ``expect_navigation`` blocks until Django responds to the POST
        # and redirects (LOGOUT_REDIRECT_URL = '/login').
        with self.page.expect_navigation(url=re.compile(r"/login")):
            sign_out.click()

        # And the session is actually gone — revisiting a protected URL
        # bounces back to /login. This is the same assertion shape as
        # the existing form-submit test, kept to ensure both tests
        # converge on the same end state.
        self.page.goto(f"{self.live_server_url}/files")
        expect(self.page).to_have_url(re.compile(r"/login"))
