"""E2E: the Properties sidebar must overlay (not steal flex space) on mobile.

Regression: the panel was an inline 320px column in the flex row with no
responsive logic, so on a < md viewport opening it squeezed the
folder-browser column to ~55px and the "+" dropdown (``flex-shrink-0``)
overflowed visually over the sidebar. A backend test cannot catch this —
the bug only manifests as a layout shift driven by Alpine state +
Tailwind's ``max-md:*`` modifiers in a real browser.
"""
from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File


class PropertiesPanelMobileOverlayTests(PlaywrightTestCase):
    """Opening the Properties panel on a phone-sized viewport must not
    shrink the folder-browser column."""

    def test_panel_overlays_folder_browser_at_mobile_width(self):
        user = self.create_user(username="mobile-user")
        self.login_as(user)

        # Seed a target so the panel's /files/properties/<uuid> request
        # has something to render. We dispatch the same event the file
        # row click handler dispatches instead of clicking a row — the
        # row affordance is not what this test is about.
        file = File.objects.create(
            owner=user, name="hello.txt", node_type=File.NodeType.FILE,
        )

        # iPhone-ish width: below the ``md`` breakpoint (768 px) so the
        # ``max-md:absolute`` rules apply. With the buggy inline layout
        # the folder-browser column would be squeezed to ~55 px here.
        self.page.set_viewport_size({"width": 375, "height": 800})
        self.page.goto(f"{self.live_server_url}/files")

        folder_browser = self.page.locator("#folder-browser")
        expect(folder_browser).to_be_visible()
        baseline_width = folder_browser.bounding_box()["width"]
        assert baseline_width > 300, (
            f"sanity: folder browser should fill the mobile viewport before "
            f"the panel opens, got {baseline_width}px"
        )

        # Open the panel the same way the row click handler does.
        self.page.evaluate(
            "(uuid) => window.dispatchEvent(new CustomEvent('open-properties', "
            "{ detail: { uuid, nodeType: 'file' } }))",
            str(file.uuid),
        )

        sidebar = self.page.locator("#properties-sidebar")
        expect(sidebar).to_be_visible()
        # Width transition is 200 ms — wait for it to settle before
        # measuring layout.
        self.page.wait_for_function(
            "() => document.getElementById('properties-sidebar').offsetWidth >= 320"
        )

        # The fix: the panel is taken out of the flex flow on < md
        # (``position: absolute``) so the folder browser keeps its full
        # width. Without the fix the panel is ``position: static`` and
        # ``baseline_width - 320 ≈ 55 px`` is what we'd measure here.
        position = sidebar.evaluate("el => getComputedStyle(el).position")
        assert position == "absolute", (
            f"expected the panel to overlay (position:absolute) below md, "
            f"got {position!r}"
        )

        post_open_width = folder_browser.bounding_box()["width"]
        assert post_open_width >= baseline_width - 1, (
            f"folder browser shrank from {baseline_width}px to "
            f"{post_open_width}px when the properties panel opened — the "
            f"panel is stealing horizontal space instead of overlaying"
        )
