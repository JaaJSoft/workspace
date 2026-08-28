"""The initials-avatar palette is written twice, in two languages.

``AVATAR_COLORS`` in ``users/ui/static/users/ui/js/user_avatar.js`` names Tailwind
classes; ``AVATAR_PALETTE`` in ``scripts/seed_demo.py`` holds the same colours
as RGB tuples, because Pillow paints the demo avatars and has no idea what a
Tailwind class is. Neither can import the other, so the only thing keeping the
two in step used to be a comment asking the next reader to remember.

These tests turn that comment into a check. The RGB values are not restated
here: they are read out of the compiled Tailwind bundle, so the seeder is
compared against what ``bg-red-500`` *actually paints* rather than against a
third hand-maintained copy of the palette. A Tailwind upgrade that shifted its
colour ramp would surface here too.

Both constants are read as source text (``ast`` for the Python one) rather than
imported: ``scripts/seed_demo.py`` calls ``django.setup()`` and pulls in faker
and Pillow at module scope, none of which a test about a colour list needs.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[3]
JS_SOURCE = (
    REPO_ROOT
    / "workspace"
    / "users"
    / "ui"
    / "static"
    / "users"
    / "ui"
    / "js"
    / "user_avatar.js"
)
SEEDER_SOURCE = REPO_ROOT / "scripts" / "seed_demo.py"
CSS_BUNDLE = REPO_ROOT / "workspace" / "common" / "static" / "css" / "app.css"


def _js_palette() -> list[str]:
    """The Tailwind class names in AVATAR_COLORS, in declaration order."""
    source = JS_SOURCE.read_text(encoding="utf-8")
    match = re.search(r"const AVATAR_COLORS = \[(.*?)\]", source, re.DOTALL)
    if match is None:
        raise AssertionError(f"AVATAR_COLORS not found in {JS_SOURCE}")
    return re.findall(r"'([^']+)'", match.group(1))


def _seeder_palette() -> list[tuple[int, ...]]:
    """The RGB tuples in AVATAR_PALETTE, in declaration order."""
    tree = ast.parse(SEEDER_SOURCE.read_text(encoding="utf-8"))
    for node in tree.body:
        targets = getattr(node, "targets", [])
        if any(getattr(t, "id", None) == "AVATAR_PALETTE" for t in targets):
            return [tuple(ast.literal_eval(elt)) for elt in node.value.elts]
    raise AssertionError(f"AVATAR_PALETTE not found in {SEEDER_SOURCE}")


def _rendered_rgb(class_name: str) -> tuple[int, int, int]:
    """What the compiled bundle actually paints for a `bg-*` utility."""
    css = CSS_BUNDLE.read_text(encoding="utf-8")
    rule = re.search(rf"\.{re.escape(class_name)}\s*\{{([^}}]*)\}}", css)
    if rule is None:
        raise AssertionError(
            f"{class_name} is absent from {CSS_BUNDLE.name}; either the class was "
            "renamed or Tailwind purged it (rebuild: cd scripts/frontend && npm run build:css)"
        )
    colour = re.search(
        r"background-color:\s*rgb\(\s*(\d+)\s+(\d+)\s+(\d+)", rule.group(1)
    )
    if colour is None:
        raise AssertionError(f"no rgb() background-color in the {class_name} rule")
    return tuple(int(c) for c in colour.groups())


class AvatarPaletteLockstepTests(SimpleTestCase):
    def test_both_palettes_hold_the_same_number_of_colours(self):
        self.assertEqual(
            len(_js_palette()),
            len(_seeder_palette()),
            "the two palettes drifted in length; a seeded avatar can now land on a "
            "colour the initials fallback never uses (or the reverse)",
        )

    def test_the_seeder_rgb_matches_what_the_tailwind_class_paints(self):
        js = _js_palette()
        seeded = _seeder_palette()

        for index, (class_name, rgb) in enumerate(zip(js, seeded, strict=True)):
            with self.subTest(index=index, class_name=class_name):
                self.assertEqual(
                    _rendered_rgb(class_name),
                    rgb,
                    f"AVATAR_PALETTE[{index}] no longer matches what {class_name} paints",
                )

    def test_every_colour_is_distinct(self):
        js = _js_palette()
        self.assertEqual(len(set(js)), len(js), "a Tailwind class is repeated")

        seeded = _seeder_palette()
        self.assertEqual(len(set(seeded)), len(seeded), "an RGB tuple is repeated")

    def test_the_palette_is_ordered_the_same_on_both_sides(self):
        # Order is not required for the two to "read as one family" — the
        # seeder and the fallback index into the palette differently on
        # purpose (see the comment on either constant). It is asserted anyway
        # because keeping the lists parallel is what makes a drift reviewable
        # as a one-line diff instead of a reshuffle nobody can eyeball.
        js = _js_palette()
        seeded = _seeder_palette()
        self.assertEqual([_rendered_rgb(c) for c in js], seeded)
