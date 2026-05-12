"""E2E: the Properties sidebar must overlay (not steal flex space) on mobile.

Two bugs are pinned here, both visible only in a real browser:

1. The original report: the panel was an inline 320 px column in the
   flex row with no responsive logic, so on a < md viewport opening it
   squeezed the folder-browser column to ~55 px and the "+" dropdown
   (``flex-shrink-0``) overflowed visually over the sidebar.

2. The follow-up report: even after the panel was made ``absolute`` on
   mobile, the fixed 320 px width left a dimmed strip of folder-browser
   visible to its left on viewports wider than ~340 px, which the user
   perceived as an unwanted gap between the collapsed left nav and the
   panel.
"""
from __future__ import annotations

import re

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File


class PropertiesPanelMobileOverlayTests(PlaywrightTestCase):
    """Opening the Properties panel on a phone-sized viewport must
    overlay the folder browser at full width."""

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
        # ``max-md:*`` rules apply. With the buggy inline layout the
        # folder-browser column would be squeezed to ~55 px here.
        self.page.set_viewport_size({"width": 375, "height": 800})
        self.page.goto(f"{self.live_server_url}/files")

        # Wait for ``sidebarCollapse().init()`` to run and collapse the
        # left nav to ``w-16`` — below the ``lg`` breakpoint it forces
        # ``collapsed=true``. Using ``expect(...).to_have_class`` auto-
        # retries (5 s by default), which is more robust than a fixed
        # timeout on a slow CI runner. Without this the folder-browser
        # column would still be measured against a ``w-72`` left nav and
        # the baseline sanity check would trip.
        expect(self.page.locator("aside").first).to_have_class(
            re.compile(r"\bw-16\b")
        )

        folder_browser = self.page.locator("#folder-browser")
        expect(folder_browser).to_be_visible()
        baseline_width = folder_browser.bounding_box()["width"]
        assert baseline_width > 250, (
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
        # Width transition is 200 ms — wait for the panel to reach its
        # final size (= the content-area width, since it covers full
        # width on mobile) before measuring layout. Waiting for less
        # than the full target races the subsequent ``bounding_box()``
        # call and intermittently reads the panel mid-transition.
        self.page.wait_for_function(
            "min => document.getElementById('properties-sidebar').offsetWidth >= min",
            arg=baseline_width - 1,
        )

        # Mechanism: the panel must be ``position: absolute`` below md
        # so it doesn't steal flex space from the folder-browser column.
        position = sidebar.evaluate("el => getComputedStyle(el).position")
        assert position == "absolute", (
            f"expected the panel to overlay (position:absolute) below md, "
            f"got {position!r}"
        )

        # User-visible outcome 1: the panel covers the full content
        # area. At a fixed 320 px width the right-aligned panel would
        # leave a dimmed strip of folder-browser visible on its left,
        # which reads as an unwanted gap between the left nav and the
        # panel.
        sidebar_width = sidebar.bounding_box()["width"]
        assert sidebar_width >= baseline_width - 1, (
            f"properties panel ({sidebar_width}px) is narrower than the "
            f"content area ({baseline_width}px) — leaves a visible gap "
            f"strip to its left"
        )

        # User-visible outcome 2: the folder-browser column keeps its
        # original width. The original bug was that a 320 px in-flow
        # panel squeezed this column to ~55 px, letting the "+" dropdown
        # overflow visually over the panel.
        post_open_width = folder_browser.bounding_box()["width"]
        assert post_open_width >= baseline_width - 1, (
            f"folder browser shrank from {baseline_width}px to "
            f"{post_open_width}px when the properties panel opened — the "
            f"panel is stealing horizontal space instead of overlaying"
        )
