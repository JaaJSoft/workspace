"""E2E: browsing a shared folder is a real navigation, one URL per node.

The listing swaps a fragment through alpine-ajax with ``x-target.push``,
so every click has to leave the visitor on an address they can copy, keep
the ``?view=`` they chose, and survive the back button. A Django test can
read the ``href`` of every link in the fragment; whether Alpine binds the
push, whether the swapped fragment keeps its own bindings, and what the
back button does afterwards only exist in a browser.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase

from ._share_fixtures import build_shared_tree


class SharedNavigationTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        build_shared_tree(self)

    def test_url_follows_into_a_subfolder_onto_a_file_and_back(self):
        base = f"{self.live_server_url}/files/shared/{self.link.token}"
        self.page.goto(f"{base}?view=grid")
        expect(self.page.locator("#shared-listing .grid")).to_be_visible()

        self.page.get_by_role("link", name="Sub").click()
        self.page.wait_for_url(lambda url: f"node={self.sub.uuid}" in url)
        self.assertIn("view=grid", self.page.url)
        expect(self.page.locator("#shared-listing .grid")).to_be_visible()
        expect(self.page.get_by_role("link", name="readme.md")).to_be_visible()

        self.page.get_by_role("link", name="readme.md").click()
        self.page.wait_for_url(lambda url: f"node={self.readme.uuid}" in url)
        expect(self.page.locator("#shared-viewer")).to_be_visible()
        expect(self.page.locator("#shared-listing")).to_have_count(0)

        self.page.go_back()
        self.page.wait_for_url(lambda url: f"node={self.sub.uuid}" in url)
        expect(self.page.get_by_role("link", name="readme.md")).to_be_visible()
        expect(self.page.locator("#shared-viewer")).to_have_count(0)
