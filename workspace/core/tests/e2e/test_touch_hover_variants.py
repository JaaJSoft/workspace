"""What the stylesheet does with hover on a touch screen.

Tailwind applies ``hover:`` and ``group-hover:`` under ``@media (hover:
hover)`` only, so a tap on a phone (which browsers turn into a sticky
emulated hover) reveals nothing; a control hidden at rest reaches a touch
screen through its ``[@media(hover:none)]:`` variant instead. Every control
is checked to declare one (core/tests/test_touch_reachable_actions.py); this
checks that the built stylesheet honours both halves of that contract.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import TOUCH_CONTEXT, PlaywrightTestCase

# Class lists copied from real controls, so the built stylesheet has them.
FIXTURE = """
<div class="group" id="touch-fixture">
  <button id="hover-only" class="opacity-0 group-hover:opacity-100">a</button>
  <button id="touch-aware" class="opacity-0 group-hover:opacity-100 [@media(hover:none)]:opacity-100">b</button>
</div>
"""


class _Fixture:
    def _load_fixture(self):
        self.page.goto(f"{self.live_server_url}/login")
        self.page.evaluate(
            "(html) => document.body.insertAdjacentHTML('afterbegin', html)", FIXTURE
        )
        return self.page.locator("#hover-only"), self.page.locator("#touch-aware")


class TouchScreenTests(_Fixture, PlaywrightTestCase):
    CONTEXT_OPTIONS = TOUCH_CONTEXT

    def test_a_tap_reveals_nothing_and_the_touch_variant_shows_the_control(self):
        hover_only, touch_aware = self._load_fixture()
        expect(touch_aware).to_have_css("opacity", "1")

        self.page.locator("#touch-fixture").tap()

        expect(hover_only).to_have_css("opacity", "0")


class PointerScreenTests(_Fixture, PlaywrightTestCase):
    def test_hover_reveals_and_the_touch_variant_stays_out_of_the_way(self):
        hover_only, touch_aware = self._load_fixture()
        # The pointer starts at the corner the fixture sits in.
        self.page.mouse.move(600, 600)
        expect(touch_aware).to_have_css("opacity", "0")

        self.page.locator("#touch-fixture").hover()

        expect(hover_only).to_have_css("opacity", "1")
        expect(touch_aware).to_have_css("opacity", "1")
