"""E2E cover for ``drawLucideIcons`` (ui/js/lucide.js) against Lucide itself.

The helper clones a per-icon template instead of letting Lucide rebuild
every svg, so its output has to stay what ``lucide.createIcons`` draws:
same attributes in the same order, same classes, same paths. Only a real
browser builds the svg elements both sides produce, so the comparison
runs in one, on the markup itself.
"""

from __future__ import annotations

from django.conf import settings

from workspace.common.tests.e2e.base import PlaywrightTestCase

STATIC = settings.BASE_DIR / "workspace" / "common" / "static" / "ui" / "js"

PLACEHOLDERS = [
    '<i data-lucide="star" class="w-4 h-4 opacity-40"></i>',
    # An alias of ellipsis-vertical.
    '<i data-lucide="more-vertical" class="w-4 h-4"></i>',
    '<i class="w-5 h-5 text-info" data-lucide="file-text"></i>',
    '<i data-lucide="bell"></i>',
    '<i data-lucide="x" aria-label="Close" class="w-4"></i>',
    '<i data-lucide="info" title="Details"></i>',
    '<i data-lucide="check" role="img"></i>',
    '<i data-lucide="check" class="lucide  w-4 w-4 lucide-check "></i>',
    '<i data-lucide="search" width="16" class="a" x-show="open" height="16"></i>',
    '<i data-lucide="arrow_right" class="w-4"></i>',
    '<i data-lucide="no-such-icon" class="w-4"></i>',
]


class LucideDrawTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.page.set_content("<!doctype html><html><body></body></html>")
        self.page.add_script_tag(path=str(STATIC / "vendor" / "lucide" / "lucide.js"))
        self.page.add_script_tag(path=str(STATIC / "lucide.js"))

    def draw_both(self, markup, then=None):
        return self.page.evaluate(
            """([markup, then]) => {
              const make = () => {
                const el = document.createElement('div');
                el.innerHTML = markup;
                document.body.appendChild(el);
                return el;
              };
              const theirs = make();
              const ours = make();
              lucide.createIcons({ root: theirs });
              drawLucideIcons(ours);
              if (then) {
                for (const root of [theirs, ours]) {
                  root.querySelectorAll('svg[data-lucide]').forEach((svg) => svg.setAttribute('data-lucide', then));
                }
                lucide.createIcons({ root: theirs });
                drawLucideIcons(ours);
              }
              return [theirs.innerHTML, ours.innerHTML];
            }""",
            [markup, then],
        )

    def test_draws_the_markup_lucide_draws(self):
        for placeholder in PLACEHOLDERS:
            with self.subTest(placeholder=placeholder):
                theirs, ours = self.draw_both(placeholder)
                self.assertEqual(ours, theirs)

    def test_every_known_icon_is_drawn(self):
        theirs, _ = self.draw_both("".join(PLACEHOLDERS))
        self.assertEqual(theirs.count("<svg"), len(PLACEHOLDERS) - 1)

    def test_redraws_an_svg_whose_icon_changed_like_lucide(self):
        theirs, ours = self.draw_both(
            '<i data-lucide="bell" class="w-4" x-show="open"></i>', "inbox"
        )
        self.assertEqual(ours, theirs)
        self.assertIn('data-lucide="inbox"', ours)

    def test_builds_each_icon_once_and_hands_back_the_svgs_carrying_directives(self):
        result = self.page.evaluate(
            """() => {
              let built = 0;
              const createElement = lucide.createElement;
              lucide.createElement = (...args) => { built += 1; return createElement(...args); };
              const root = document.createElement('div');
              root.innerHTML = '<i data-lucide="star"></i>'.repeat(50)
                + '<i data-lucide="bell" class="w-4"></i>'.repeat(50)
                + '<i data-lucide="bell" x-show="open"></i><i data-lucide="star" :class="{ on: open }"></i>';
              document.body.appendChild(root);
              const bound = drawLucideIcons(root);
              const svgs = root.querySelectorAll('svg');
              // Clones, not one node moved around: editing one leaves the rest.
              svgs[0].firstElementChild.setAttribute('d', 'M0 0');
              return {
                built,
                svgs: svgs.length,
                distinctPaths: new Set(Array.from(svgs).slice(0, 50).map((s) => s.firstElementChild.getAttribute('d'))).size,
                bound: bound.map((svg) => svg.getAttribute('data-lucide')),
              };
            }"""
        )
        self.assertEqual(result["built"], 2)
        self.assertEqual(result["svgs"], 102)
        self.assertEqual(result["distinctPaths"], 2)
        self.assertEqual(result["bound"], ["bell", "star"])


class ElementIconScopeTests(PlaywrightTestCase):
    """An element hydrating its own icons leaves the rest of the page alone.

    ``createIcons`` ignores a ``nodes`` option and falls back to the whole
    document, so an element passing one redrew every icon on the page each
    time it connected: once per tag chip of a tagged listing.
    """

    def setUp(self):
        super().setUp()
        self.page.set_content(
            '<!doctype html><html><body><i data-lucide="bell" id="bystander"></i></body></html>'
        )
        for script in ("vendor/lucide/lucide.js", "tag_chip.js", "inline_alert.js"):
            self.page.add_script_tag(path=str(STATIC / script))
        self.page.evaluate(
            "() => { lucide.createIcons(); window.__bystander = document.getElementById('bystander'); }"
        )

    def connect(self, markup):
        return self.page.evaluate(
            """(markup) => {
              const host = document.createElement('div');
              host.innerHTML = markup;
              document.body.appendChild(host);
              return {
                drawn: host.querySelectorAll('svg[data-lucide]').length,
                bystanderKept: document.getElementById('bystander') === window.__bystander,
              };
            }""",
            markup,
        )

    def test_a_tag_chip_draws_only_its_own_icon(self):
        for markup in (
            '<tag-chip name="Urgent" icon="flame"></tag-chip>',
            '<tag-chip name="Urgent" icon="flame" compact></tag-chip>',
        ):
            with self.subTest(markup=markup):
                self.assertEqual(
                    self.connect(markup), {"drawn": 1, "bystanderKept": True}
                )

    def test_an_inline_alert_draws_only_its_own_icon(self):
        self.assertEqual(
            self.connect(
                '<inline-alert type="info" message="Heads up"></inline-alert>'
            ),
            {"drawn": 1, "bystanderKept": True},
        )
