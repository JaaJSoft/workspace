"""End-to-end tests for the login page.

These tests also validate the `login_as` and `login_via_ui` helpers on
``workspace.common.tests.e2e.base.PlaywrightTestCase`` — if they pass,
both helpers work against the real app.

Skipped unless ``E2E=1`` is set (see the base class docstring).
"""
from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase


class LoginPageTests(PlaywrightTestCase):
    """Covers the /login page happy path, error path, and `?next=` redirect."""

    def test_valid_credentials_redirects_to_home(self):
        self.create_user(username="alice", password="pass12345")

        self.login_via_ui("alice", "pass12345")

        # LOGIN_REDIRECT_URL is '/' → we should land on the dashboard, not /login.
        assert "/login" not in self.page.url, (
            f"expected to be redirected off /login, still at {self.page.url}"
        )

    def test_invalid_credentials_shows_error(self):
        self.create_user(username="bob", password="correct-horse")

        self.login_via_ui("bob", "wrong-password", wait_for_redirect=False)

        # The error alert rendered by `users/ui/auth/login.html` when
        # `form.errors` is truthy. We match on the text, not the Tailwind
        # class, because the class may change without changing behavior.
        expect(
            self.page.get_by_text(
                "Invalid username or password", exact=False
            )
        ).to_be_visible()

        # And we must still be on /login — the form didn't redirect.
        assert "/login" in self.page.url, (
            f"expected to still be on /login, at {self.page.url}"
        )

    def test_next_parameter_redirects_after_login(self):
        import re

        self.create_user(username="carol", password="pass12345")

        # Navigate to the login page with ?next=/calendar so Django's
        # LoginView honors it on successful submission.
        self.page.goto(f"{self.live_server_url}/login?next=/calendar")
        self.page.locator('input[name="username"]').fill("carol")
        self.page.locator('input[name="password"]').fill("pass12345")
        self.page.get_by_role(
            "button", name=re.compile("Sign In", re.I)
        ).click()

        # After submit we should land on /calendar, not the LOGIN_REDIRECT_URL.
        self.page.wait_for_url(lambda url: "/login" not in url)
        assert self.page.url.rstrip("/").endswith("/calendar"), (
            f"expected to be redirected to /calendar, got {self.page.url}"
        )

    def test_login_as_helper_skips_form(self):
        user = self.create_user(username="dave", password="pass12345")

        # No form submission — straight session-cookie injection.
        self.login_as(user)

        # Dashboard (/) requires @login_required; if the cookie works the
        # page loads, otherwise Django redirects to /login?next=/.
        self.page.goto(f"{self.live_server_url}/")
        assert "/login" not in self.page.url, (
            f"login_as failed to authenticate: redirected to {self.page.url}"
        )
