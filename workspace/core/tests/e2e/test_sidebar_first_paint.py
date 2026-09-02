"""The module sidebar must paint at its final width before Alpine runs.

Alpine is loaded with ``defer``, so it binds ``:class`` only after the whole
document is parsed - after the first paint on any page of real size. A sidebar
whose width lives only in that binding therefore shows up at whatever width the
raw HTML gives it (content-sized, or the mobile rail) and snaps to its real
width a few frames later, shifting the entire page with it.

The server knows the collapsed preference, so the ``<aside>`` carries its final
width class in the HTML it sends; Alpine's binding then changes nothing on
mount. Two observations pin that down, both installed before any document
script runs: the class attribute the ``<aside>`` wears the instant the parser
inserts it (a MutationObserver fires on that, long before deferred scripts), and
every distinct width it is painted at (a requestAnimationFrame sampler). The
class check is deterministic; the width samples are the visual proof and also
catch a static class that loses a specificity fight with the bound one.
"""

from __future__ import annotations

from django.core.cache import cache

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.projects.services.projects import create_project
from workspace.users.services.settings import set_setting

DESKTOP = {"width": 1280, "height": 900}

# Width utility each module's sidebar wears when expanded. The drawers that
# stay open on mobile (files, chat) always carry the `w-16` rail and open on
# desktop through a `lg:` variant; the others swap the two.
EXPANDED = {
    "files": "lg:w-72",
    "notes": "w-72",
    "mail": "w-72",
    "calendar": "w-72",
    "projects": "w-72",
    "chat": "lg:!w-80",
}
COLLAPSED = "w-16"

# Drawers that are off-canvas below `lg` (the `lg:drawer-open` shells).
OFF_CANVAS_ON_MOBILE = ("notes", "mail", "calendar", "projects")
MOBILE = {"width": 375, "height": 667}
FULL_WIDTH = 288  # w-72
OPEN_DRAWER = """() => {
  const toggle = document.querySelector('input.drawer-toggle');
  toggle.checked = true;
  toggle.dispatchEvent(new Event('change', { bubbles: true }));
}"""

OBSERVER = """
window.__aside = { firstClass: null, widths: [] };
const seen = () => {
  const aside = document.querySelector('.drawer-side aside');
  if (aside && window.__aside.firstClass === null) {
    window.__aside.firstClass = aside.className;
  }
};
// The script runs before <html> exists, so the document is the only node
// there is to observe.
new MutationObserver(seen).observe(document, { childList: true, subtree: true });
(function sample() {
  const aside = document.querySelector('.drawer-side aside');
  if (aside) {
    const width = Math.round(aside.getBoundingClientRect().width);
    const widths = window.__aside.widths;
    if (widths[widths.length - 1] !== width) widths.push(width);
  }
  if (performance.now() < 5000) requestAnimationFrame(sample);
})();
"""


class SidebarFirstPaintTests(PlaywrightTestCase):
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
        self.page.set_viewport_size(DESKTOP)
        self.context.add_init_script(OBSERVER)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _load(self, module):
        self.page.goto(f"{self.live_server_url}{self.urls[module]}")
        self.page.wait_for_selector(".drawer-side aside")
        # Long enough for Alpine to bind and for a width transition to end.
        self.page.wait_for_timeout(1200)
        aside = self.page.locator(".drawer-side aside").first
        return {
            "module": module,
            "first": self.page.evaluate("window.__aside.firstClass").split(),
            "final": aside.get_attribute("class").split(),
            "widths": self.page.evaluate("window.__aside.widths"),
        }

    def _assert_stable(self, seen, width_class):
        self.assertIn(width_class, seen["first"], seen)
        self.assertIn(width_class, seen["final"], seen)
        self.assertEqual(len(seen["widths"]), 1, seen)

    def test_expanded_sidebar_paints_at_its_width_before_alpine(self):
        for module, width_class in EXPANDED.items():
            with self.subTest(module=module):
                self._assert_stable(self._load(module), width_class)

    def test_collapsed_sidebar_paints_as_the_rail_before_alpine(self):
        for module in EXPANDED:
            set_setting(self.user, module, "sidebar_collapsed", True)
        for module in EXPANDED:
            with self.subTest(module=module):
                seen = self._load(module)
                self._assert_stable(seen, COLLAPSED)
                self.assertNotIn(EXPANDED[module], seen["final"], seen)

    def test_mobile_drawer_opens_at_full_width_whatever_the_preference(self):
        # Below `lg` these drawers are off-canvas until the hamburger opens
        # them, and an opened one is the full sidebar: the desktop
        # preference must not turn it into a 64px icon rail on a phone.
        for module in OFF_CANVAS_ON_MOBILE:
            set_setting(self.user, module, "sidebar_collapsed", True)
        self.page.set_viewport_size(MOBILE)
        for module in OFF_CANVAS_ON_MOBILE:
            with self.subTest(module=module):
                self.page.goto(f"{self.live_server_url}{self.urls[module]}")
                self.page.wait_for_selector(".drawer-side aside")
                self.page.wait_for_timeout(600)
                self.page.evaluate(OPEN_DRAWER)
                self.page.wait_for_timeout(600)
                box = self.page.locator(".drawer-side aside").first.bounding_box()
                self.assertEqual(round(box["x"]), 0, box)
                self.assertEqual(round(box["width"]), FULL_WIDTH, box)
