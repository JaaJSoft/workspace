"""E2E test: the profile page renders the user's full name when set.

The profile template (``profile.html``) picks between
``first_name + last_name`` and ``username`` for the main ``<h1>``. This
test proves the full-name branch is exercised end-to-end — template
extension, context injection, and rendering.
"""
from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase


class ProfileFullNameTests(PlaywrightTestCase):
    """When first/last name are set, the profile <h1> shows them."""

    def test_profile_heading_shows_full_name(self):
        user = self.create_user(
            username="alice",
            first_name="Pierre",
            last_name="Chopinet",
        )
        self.login_as(user)
        self.page.goto(f"{self.live_server_url}/users/profile")
        expect(
            self.page.get_by_role("heading", level=1)
        ).to_contain_text("Pierre Chopinet")
