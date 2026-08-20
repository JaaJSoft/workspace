"""E2E test: the sidebar collapse button's icon flips when toggled.

The collapse/expand button lives in the shared ``drawer_item.html``
partial, so every module's sidebar (files, chat, mail, notes, calendar,
projects) renders the same markup - the files page stands in for all of
them.

The bug class this guards against: Lucide hydrates ``<i data-lucide>``
into an ``<svg>`` exactly once, at page load. A reactive
``:data-lucide="collapsed ? 'panel-left-open' : 'panel-left-close'"``
binding therefore only updates the *attribute* on the already-drawn svg;
the paths never change, and the icon points the wrong way until a full
reload re-runs ``lucide.createIcons()``. The partial works around this
by rendering both direction icons statically and toggling them with
``x-show`` - this test breaks if anyone reintroduces a dynamic
``:data-lucide`` binding there.

Because the broken variant *does* keep the ``data-lucide`` attribute
up to date on the stale svg, asserting on the attribute alone would
pass against buggy code. The test therefore also compares the visible
icon's drawn content (``inner_html``) across the toggle.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase


class SidebarCollapseIconTests(PlaywrightTestCase):
    def test_collapse_button_icon_flips_without_reload(self):
        user = self.create_user(username="alice")
        self.login_as(user)

        # Desktop viewport: below the ``lg`` breakpoint ``toggleCollapse()``
        # early-returns and the button does nothing.
        self.page.set_viewport_size({"width": 1280, "height": 800})
        self.page.goto(f"{self.live_server_url}/files")

        button = self.page.get_by_role("button", name="Collapse sidebar")

        # Precondition: Lucide hydrated the icon. If this times out, the
        # icon CDN did not load and the rest of the test would measure
        # nothing - fail loudly here rather than misattribute it below.
        close_icon = button.locator("svg[data-lucide='panel-left-close']")
        expect(close_icon).to_be_visible()
        close_paths = close_icon.inner_html()

        button.click()

        # The accessible name flips with the state...
        button = self.page.get_by_role("button", name="Expand sidebar")
        # ...and so must the icon: 'panel-left-open' visible, the old
        # direction hidden.
        open_icon = button.locator("svg[data-lucide='panel-left-open']")
        expect(open_icon).to_be_visible()
        expect(button.locator("svg[data-lucide='panel-left-close']")).to_be_hidden()

        # Guard against the stale-svg failure mode: an svg whose
        # data-lucide attribute updated but whose drawn paths did not.
        self.assertNotEqual(
            open_icon.inner_html(),
            close_paths,
            "the visible icon's svg content did not change on toggle - "
            "Lucide is still showing the stale hydrated icon",
        )

        # Round-trip: expanding again restores the original icon.
        button.click()
        button = self.page.get_by_role("button", name="Collapse sidebar")
        expect(button.locator("svg[data-lucide='panel-left-close']")).to_be_visible()
        expect(button.locator("svg[data-lucide='panel-left-open']")).to_be_hidden()
