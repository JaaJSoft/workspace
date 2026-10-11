"""Catch a page that redraws every Lucide icon of the document.

A bare ``lucide.createIcons()`` - or ``initLucideIcons()``, or
``createIcons({ nodes })``, which Lucide ignores - replaces every
``[data-lucide]`` element of the document, already drawn svgs included, so
the cost of an update grows with the whole page instead of with what changed.

``IconScopeMixin`` plants a bystander icon in the page as soon as its body
is parsed, so the page's own first draw turns it into an svg like any
server-rendered icon, and counts every time that svg is replaced afterwards.
Nothing scoped ever reaches it: it sits in a wrapper of its own at the top of
the body, outside every component.
"""

from __future__ import annotations

BYSTANDER_SCRIPT = """(() => {
  window.__bystanderRedraws = 0;
  let planted = false;
  const plant = () => {
    const host = document.createElement('div');
    host.innerHTML = '<i data-lucide="bell" id="lucide-bystander"></i>';
    document.body.appendChild(host);
  };
  new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      for (const node of mutation.removedNodes) {
        if (node instanceof SVGElement && node.id === 'lucide-bystander') {
          window.__bystanderRedraws += 1;
        }
      }
    }
    if (!planted && document.body) {
      planted = true;
      plant();
    }
  }).observe(document, { childList: true, subtree: true });
})();"""

# Two frames: the icon observer draws on the next one, a $nextTick lands before.
SETTLE = (
    "() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))"
)


class IconScopeMixin:
    """For a ``PlaywrightTestCase``: mix in before it, ``super().setUp()`` first."""

    def setUp(self):
        super().setUp()
        self.context.add_init_script(BYSTANDER_SCRIPT)

    def settle_icons(self):
        self.page.wait_for_load_state("networkidle")
        self.page.evaluate(SETTLE)
        self.assertTrue(
            self.page.evaluate(
                "() => document.getElementById('lucide-bystander') instanceof SVGElement"
            ),
            "the page never drew the bystander icon",
        )

    def forget_bystander_redraws(self):
        """Start counting from here: call once the page has settled."""
        self.settle_icons()
        self.page.evaluate("() => { window.__bystanderRedraws = 0; }")

    def assert_bystander_kept(self):
        self.settle_icons()
        self.assertEqual(
            self.page.evaluate("() => window.__bystanderRedraws"),
            0,
            "the page redrew every icon of the document",
        )
