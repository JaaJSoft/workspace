"""The icon rail must never paint its expanded content before Alpine runs.

The aside's width is server-rendered (see test_sidebar_first_paint), but the
header and the drawer items inside it used to hide their labels with ``x-show``
alone: on the 64px rail - a phone, or a desktop with the module's collapsed
preference - the module name, its chevron and every item label painted in full,
overflowing the rail, until deferred Alpine bound them a few frames later.

``drawer_item.html``, ``module_switcher.html`` and ``preferences_popover.html``
now take the server-known state (``collapsed_initial``, ``mobile_rail``) and put
the collapsed classes in the HTML itself, with object-form ``:class`` bindings
so Alpine drops them on mount. The observer below, installed before any
document script runs, measures the aside on every parser mutation until Alpine
appears (``window.Alpine`` is defined the moment the deferred bundle executes)
and keeps the widest overflow any visible descendant ever had past the aside's
right edge. That number must stay at zero - and stay at zero after Alpine
binds, and the labels must still come and go when the rail is toggled, or the
static classes would have been left behind.
"""

from __future__ import annotations

from django.core.cache import cache
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.projects.services.projects import create_project
from workspace.users.services.settings import set_setting

DESKTOP = {"width": 1280, "height": 900}
MOBILE = {"width": 375, "height": 667}

MODULES = ("files", "notes", "chat", "mail", "calendar", "projects")
# Drawers that stay open below `lg` as the rail; the others are off-canvas
# there, so their content is not painted at all until the drawer opens.
RAIL_ON_MOBILE = ("files", "chat")

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


class SidebarRailFirstPaintTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")
        project = create_project(self.user, name="Roadmap")
        self.urls = {
            "files": "/files",
            "notes": "/notes",
            "chat": "/chat",
            "mail": "/mail",
            "calendar": "/calendar",
            "projects": f"/projects/{project.uuid}",
        }
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
            set_setting(self.user, module, "sidebar_collapsed", True)
        self.page.set_viewport_size(DESKTOP)
        for module in MODULES:
            with self.subTest(module=module):
                self._assert_within_rail(self._load(module))

    def test_mobile_rail_paints_without_labels_whatever_the_preference(self):
        self.page.set_viewport_size(MOBILE)
        for module in RAIL_ON_MOBILE:
            with self.subTest(module=module):
                self._assert_within_rail(self._load(module))

    def _help_label(self):
        return self.page.locator(".drawer-side aside").get_by_text("Help", exact=True)

    def test_expanded_sidebar_still_shows_its_labels_and_collapses(self):
        # The static classes are a first-paint aid only: once Alpine binds,
        # the expanded sidebar must show its labels and the toggle must hide
        # them, both on a rail drawer and on an off-canvas one.
        self.page.set_viewport_size(DESKTOP)
        for module in ("files", "mail"):
            with self.subTest(module=module):
                self._load(module)
                expect(self._help_label()).to_be_visible()
                self.page.get_by_role("button", name="Collapse sidebar").click()
                expect(self._help_label()).to_be_hidden()
                self.assertLessEqual(self.page.evaluate(MEASURE_NOW)["overflow"], 1)

    def test_collapsed_sidebar_expands_and_shows_its_labels(self):
        # The inverse: the server-rendered `hidden` must go away on expand,
        # or the labels would be stuck hidden for the rest of the session.
        for module in ("files", "mail"):
            set_setting(self.user, module, "sidebar_collapsed", True)
        self.page.set_viewport_size(DESKTOP)
        for module in ("files", "mail"):
            with self.subTest(module=module):
                self._load(module)
                expect(self._help_label()).to_be_hidden()
                self.page.get_by_role("button", name="Expand sidebar").click()
                expect(self._help_label()).to_be_visible()
