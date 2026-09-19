"""The people sidebar's icon rail must never paint its expanded content before Alpine runs.

Duplicated from ``workspace/core/tests/e2e/test_sidebar_rail_first_paint.py`` and
restricted to the ``people`` module: each module's e2e job runs alone, so this
module needs its own copy of the observer and assertions rather than importing
the core test. See that file for the full rationale - the aside's width is
server-rendered and ``core``'s own ``test_sidebar_first_paint.py`` measures it
for every module, ``people`` included, but the header and the drawer items
inside it must also hide their labels in the HTML itself, or the rail paints
them in full for a few frames until deferred Alpine binds the ``:class``
bindings that would otherwise hide them.

The people drawer is off-canvas below ``lg`` (``lg:drawer-open``), not a rail
like files/chat, so nothing in it paints on a phone before the drawer opens -
there is no mobile rail here to measure, and the desktop cases below are the
whole story.
"""

from __future__ import annotations

from django.core.cache import cache
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.core.setting_keys import SIDEBAR_COLLAPSED
from workspace.users.services.settings import set_setting

DESKTOP = {"width": 1280, "height": 900}

MODULES = ("people",)
urls = {"people": "/people"}

OBSERVER = """
window.__rail = { overflow: 0, offenders: [], measured: 0, text: '' };
// Widest visible overflow past the aside's right edge. checkVisibility
// follows the ancestors, so a row folded away under a `max-h-0 opacity-0
// overflow-hidden` parent does not count - it is not painted either.
window.__railMeasure = () => {
  const aside = document.querySelector('.drawer-side aside');
  if (!aside) return;
  const edge = aside.getBoundingClientRect().right;
  for (const el of aside.querySelectorAll('*')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0) continue;
    if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true,
                              opacityProperty: true, visibilityProperty: true })) continue;
    const over = Math.round(r.right - edge);
    if (over > window.__rail.overflow) {
      window.__rail.overflow = over;
      window.__rail.offenders.push(
        el.tagName.toLowerCase() + '.' + el.className + ' +' + over + 'px');
    }
  }
  // innerText follows display and visibility, so this is what a reader
  // could see on the rail at that step: a label that wraps inside the 40px
  // item never crosses the edge, but it is still painted.
  window.__rail.text = aside.innerText;
  window.__rail.measured += 1;
};
// The script runs before <html> exists, so the document is the only node
// there is to observe. Every mutation before Alpine is a parser step, and
// each one is a state the page could have painted.
const observer = new MutationObserver(() => {
  if (window.Alpine) { observer.disconnect(); return; }
  window.__railMeasure();
});
observer.observe(document, { childList: true, subtree: true });
"""

MEASURE_NOW = """() => {
  window.__rail = { overflow: 0, offenders: [], measured: 0, text: '' };
  window.__railMeasure();
  return window.__rail;
}"""

# Clicks the sidebar toggle and measures the aside in the same task: Alpine
# applies the `:class` bindings in a microtask, and `x-show` hides on the next
# animation frame, so this is the state that a paint or a Playwright call can
# catch between the aside narrowing and a row hidden by `x-show` alone
# following it. Everything that leaves with the rail must leave here. The
# people toggle button carries no aria-label (unlike files/mail, which build
# it through drawer_item.html), only a bound `:title`, so the lookup matches
# on that attribute instead.
TOGGLE_AND_MEASURE = """async (title) => {
  document.querySelector('.drawer-side aside [title="' + title + '"]').click();
  for (let i = 0; i < 10; i++) await Promise.resolve();
  window.__rail = { overflow: 0, offenders: [], measured: 0, text: '' };
  window.__railMeasure();
  return window.__rail;
}"""


class PeopleSidebarFirstPaintTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")
        self.urls = dict(urls)
        self.login_as(self.user)
        self.context.add_init_script(OBSERVER)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _load(self, module):
        self.page.goto(f"{self.live_server_url}{self.urls[module]}")
        self.page.wait_for_selector(".drawer-side aside")
        # Long enough for Alpine to bind and for the width transition to end.
        self.page.wait_for_timeout(1200)
        before = self.page.evaluate("window.__rail")
        after = self.page.evaluate(MEASURE_NOW)
        return {"module": module, "before_alpine": before, "after_alpine": after}

    def _assert_within_rail(self, seen):
        before, after = seen["before_alpine"], seen["after_alpine"]
        self.assertGreater(before["measured"], 0, seen)
        self.assertLessEqual(before["overflow"], 1, seen)
        self.assertLessEqual(after["overflow"], 1, seen)
        # Whatever text the rail showed before Alpine must still be there
        # after it: text that Alpine takes away was a flicker.
        shown_before = {line.strip() for line in before["text"].split("\n")}
        shown_after = {line.strip() for line in after["text"].split("\n")}
        self.assertLessEqual(shown_before - {""}, shown_after, seen)

    def test_collapsed_preference_paints_the_rail_without_labels(self):
        for module in MODULES:
            set_setting(self.user, module, SIDEBAR_COLLAPSED, True)
        self.page.set_viewport_size(DESKTOP)
        for module in MODULES:
            with self.subTest(module=module):
                self._assert_within_rail(self._load(module))

    def _all_label(self):
        return self.page.locator(".drawer-side aside").get_by_text("All", exact=True)

    def test_expanded_sidebar_still_shows_its_labels_and_collapses(self):
        # The static classes are a first-paint aid only: once Alpine binds,
        # the expanded sidebar must show its labels and the toggle must hide
        # them. The rail must be clean the moment the aside narrows, not a
        # frame later.
        self.page.set_viewport_size(DESKTOP)
        for module in MODULES:
            with self.subTest(module=module):
                self._load(module)
                expect(self._all_label()).to_be_visible()
                same_task = self.page.evaluate(TOGGLE_AND_MEASURE, "Collapse")
                self.assertLessEqual(same_task["overflow"], 1, same_task)
                expect(self._all_label()).to_be_hidden()
                settled = self.page.evaluate(MEASURE_NOW)
                self.assertLessEqual(settled["overflow"], 1, settled)

    def test_collapsed_sidebar_expands_and_shows_its_labels(self):
        # The inverse: the server-rendered `hidden` must go away on expand,
        # or the labels would be stuck hidden for the rest of the session.
        for module in MODULES:
            set_setting(self.user, module, SIDEBAR_COLLAPSED, True)
        self.page.set_viewport_size(DESKTOP)
        for module in MODULES:
            with self.subTest(module=module):
                self._load(module)
                expect(self._all_label()).to_be_hidden()
                self.page.locator(".drawer-side aside").get_by_title("Expand").click()
                expect(self._all_label()).to_be_visible()
