"""E2E: a password-protected link carries its token through every click.

The gate puts the access token in the URL, and every link the listing
renders (folders, files, breadcrumbs, the view toggle) has to carry it,
or the first click lands the visitor back on the password card. A Django
test sees the token in each ``href``; only a browser proves that the
fragment swap, the pushed URL and the viewer all still have it after the
visitor has moved twice.
"""

from __future__ import annotations

from django.contrib.auth.hashers import make_password
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase

from ._share_fixtures import build_shared_tree

PASSWORD = "s3cret-pass"


class SharedPasswordTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        build_shared_tree(self, password=make_password(PASSWORD))

    def test_token_survives_navigation_after_the_gate(self):
        self.page.goto(f"{self.live_server_url}/files/shared/{self.link.token}")
        expect(self.page.get_by_text("Protected link")).to_be_visible()
        # Nothing behind the gate leaks into the gate page.
        self.assertNotIn("Sub", self.page.content())

        field = self.page.get_by_placeholder("Password")
        field.fill("wrong")
        field.press("Enter")
        expect(self.page.get_by_text("Invalid password")).to_be_visible()

        field.fill(PASSWORD)
        field.press("Enter")
        self.page.wait_for_url(lambda url: "access_token=" in url)
        expect(self.page.get_by_role("link", name="Sub")).to_be_visible()

        self.page.get_by_role("link", name="Sub").click()
        self.page.wait_for_url(lambda url: f"node={self.sub.uuid}" in url)
        self.assertIn("access_token=", self.page.url)
        expect(self.page.get_by_role("link", name="readme.md")).to_be_visible()

        self.page.get_by_role("link", name="readme.md").click()
        self.page.wait_for_url(lambda url: f"node={self.readme.uuid}" in url)
        self.assertIn("access_token=", self.page.url)
        expect(self.page.locator("#shared-viewer")).to_be_visible()
        expect(self.page.get_by_text("Protected link")).to_have_count(0)
