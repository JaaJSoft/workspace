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

    def test_navigation_buttons_move_through_the_visit(self):
        """The Back, Forward and Up buttons next to the breadcrumb drive
        the same history the links build. Their enabled state and what
        they fetch are Alpine bindings over a JS stack; only a browser
        runs them."""
        base = f"{self.live_server_url}/files/shared/{self.link.token}"
        self.page.goto(base)
        back = self.page.get_by_role("button", name="Back")
        forward = self.page.get_by_role("button", name="Forward")
        up = self.page.get_by_role("button", name="Up")
        # At the share root there is nowhere to go yet.
        expect(back).to_be_disabled()
        expect(forward).to_be_disabled()
        expect(up).to_be_disabled()

        self.page.get_by_role("link", name="Sub").click()
        self.page.wait_for_url(lambda url: f"node={self.sub.uuid}" in url)
        self.page.get_by_role("link", name="readme.md").click()
        self.page.wait_for_url(lambda url: f"node={self.readme.uuid}" in url)
        expect(up).to_be_enabled()

        # Up from a file is its folder.
        up.click()
        self.page.wait_for_url(lambda url: f"node={self.sub.uuid}" in url)
        expect(self.page.get_by_role("link", name="readme.md")).to_be_visible()

        # Back returns to the file, Forward to the folder again.
        expect(back).to_be_enabled()
        back.click()
        self.page.wait_for_url(lambda url: f"node={self.readme.uuid}" in url)
        expect(self.page.locator("#shared-viewer")).to_be_visible()
        expect(forward).to_be_enabled()
        forward.click()
        self.page.wait_for_url(lambda url: f"node={self.sub.uuid}" in url)
        expect(self.page.locator("#shared-listing")).to_be_visible()
        expect(forward).to_be_disabled()

        # Up again lands on the root (addressed by its own uuid, like any
        # node), where Up is disabled again.
        up.click()
        self.page.wait_for_url(lambda url: f"node={self.root.uuid}" in url)
        expect(self.page.get_by_role("link", name="Sub")).to_be_visible()
        expect(up).to_be_disabled()
