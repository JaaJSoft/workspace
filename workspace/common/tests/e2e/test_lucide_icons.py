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

# The gettext() the JavaScript catalog defines, in the source language.
UNTRANSLATED_CATALOG = "window.gettext = (msgid) => msgid;"

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

    def test_draws_a_root_that_is_itself_an_icon(self):
        theirs, _ = self.draw_both('<i data-lucide="star" class="w-4"></i>')
        ours = self.page.evaluate(
            """() => {
              const host = document.createElement('div');
              host.innerHTML = '<i data-lucide="star" class="w-4"></i>';
              document.body.appendChild(host);
              drawLucideIcons(host.firstElementChild);
              return host.innerHTML;
            }"""
        )
        self.assertEqual(ours, theirs)


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
        # A real page loads the JavaScript catalog before these scripts, and
        # tag_chip.js translates its palette as soon as it loads.
        self.page.add_script_tag(content=UNTRANSLATED_CATALOG)
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

    def test_a_tag_chip_draws_its_trailing_mark_after_the_label(self):
        state = self.page.evaluate(
            """() => {
              const chip = document.createElement('tag-chip');
              chip.setAttribute('name', 'Dog');
              chip.setAttribute('trailing-icon', 'sparkles');
              chip.setAttribute('trailing-label', 'Added automatically');
              document.body.appendChild(chip);
              const marked = {
                order: Array.from(chip.children, (el) => el.tagName),
                mark: chip.querySelector('[role="img"]')?.getAttribute('aria-label'),
                drawn: chip.querySelectorAll('svg[data-lucide="sparkles"]').length,
                title: chip.title,
              };
              chip.removeAttribute('trailing-icon');
              return {
                marked,
                unmarked: {
                  drawn: chip.querySelectorAll('svg').length,
                  title: chip.getAttribute('title'),
                },
              };
            }"""
        )

        self.assertEqual(
            state["marked"],
            {
                "order": ["SPAN", "SPAN"],
                "mark": "Added automatically",
                "drawn": 1,
                "title": "Added automatically",
            },
        )
        self.assertEqual(state["unmarked"], {"drawn": 0, "title": None})

    def test_an_inline_alert_draws_only_its_own_icon(self):
        self.assertEqual(
            self.connect(
                '<inline-alert type="info" message="Heads up"></inline-alert>'
            ),
            {"drawn": 1, "bystanderKept": True},
        )


class ObserverScopeTests(PlaywrightTestCase):
    """The observer draws what changed, not the container around it.

    Each case changes one icon or adds one subtree, then checks every svg
    drawn before it is still the same node: a redraw of the parent would
    have replaced the siblings too - every row of a listing for one added
    row, every icon of the page for one element added under the body.
    """

    def setUp(self):
        super().setUp()
        self.page.set_content(
            "<!doctype html><html><body>"
            '<ul id="list"><li><i data-lucide="star"></i> A</li><li><i data-lucide="star"></i> B</li></ul>'
            '<span><i data-lucide="chevron-down" id="toggle"></i><i data-lucide="x"></i></span>'
            "</body></html>"
        )
        self.page.add_script_tag(path=str(STATIC / "vendor" / "lucide" / "lucide.js"))
        self.page.add_script_tag(path=str(STATIC / "lucide.js"))
        self.page.evaluate(
            """() => {
              initLucideIcons();
              observeLucideIcons();
              window.__drawn = Array.from(document.querySelectorAll('svg[data-lucide]'));
            }"""
        )

    def change(self, script, drawn):
        self.page.evaluate(script)
        self.page.wait_for_selector(drawn, state="attached")
        return self.page.evaluate(
            "() => window.__drawn.filter((svg) => !document.contains(svg)).length"
        )

    def test_a_row_added_to_a_list_leaves_the_other_rows_alone(self):
        replaced = self.change(
            """() => {
              const row = document.createElement('li');
              row.innerHTML = '<i data-lucide="bell"></i> C';
              document.getElementById('list').appendChild(row);
            }""",
            '#list svg[data-lucide="bell"]',
        )
        self.assertEqual(replaced, 0)

    def test_an_icon_added_under_the_body_leaves_the_page_alone(self):
        replaced = self.change(
            """() => {
              const icon = document.createElement('i');
              icon.setAttribute('data-lucide', 'inbox');
              document.body.appendChild(icon);
            }""",
            'body > svg[data-lucide="inbox"]',
        )
        self.assertEqual(replaced, 0)

    def test_an_icon_renamed_in_place_leaves_its_neighbours_alone(self):
        replaced = self.change(
            "() => document.getElementById('toggle').setAttribute('data-lucide', 'chevron-right')",
            # The renamed svg matches a data-lucide selector before it is
            # redrawn; only the new drawing carries the icon's class.
            "svg.lucide-chevron-right",
        )
        # The renamed svg itself is the one node that had to go.
        self.assertEqual(replaced, 1)
        self.assertEqual(
            self.page.evaluate(
                "() => document.getElementById('toggle').getAttribute('class')"
            ),
            "lucide lucide-chevron-right",
        )
