"""A control revealed on hover must say what happens on a touch screen.

Tailwind applies ``hover:`` and ``group-hover:`` only under
``@media (hover: hover)``, so on a phone or a tablet an element hidden at rest
(``opacity-0``, ``hidden``, ``invisible``) and revealed by a hover variant is
never revealed at all. The element has to name its touch behaviour with a
``[@media(hover:none)]:`` variant on the same class list:

* ``[@media(hover:none)]:opacity-100`` (or ``:inline-flex``...) keeps a small,
  sparse control on screen, like a "more" button on a list row;
* ``[@media(hover:none)]:hidden`` takes it away where showing it on every row
  would clutter the screen, provided the same action is reached another way
  (a tap that focuses the row, a long press, the row's menu).

Purely decorative reveals (a drag handle, a chevron, a caption) do nothing on
a tap and are listed in DECORATIVE with the reason they need no touch path.

The check reads one line at a time: a class list sits on one line in the
templates and in the JS that builds elements, which is what keeps it simple.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

WORKSPACE = Path(__file__).resolve().parents[2]

HIDING = {"opacity-0", "hidden", "invisible"}
REVEALING = re.compile(
    r"opacity-[1-9]\d*|visible|block|flex|inline-flex|inline-block|inline|pointer-events-auto"
)
HOVER_VARIANT = re.compile(r"^(?:group-hover(?:/[\w-]+)?|hover)$")
TOUCH_VARIANT = "[@media(hover:none)]"
TOKEN_SEPARATORS = re.compile(r"[\s'\"`]+")

# (path, substring of the line, why a touch screen needs nothing)
DECORATIVE = [
    (
        "calendar/ui/templates/calendar/ui/partials/_poll_modals.html",
        "w-4 h-4 opacity-0 group-hover:opacity-40 transition-opacity",
        "chevron hinting that the row opens; the row itself is the tap target",
    ),
    (
        "files/ui/templates/files/ui/partials/_sidebar_folder_item.html",
        "opacity-0 group-hover:opacity-40 transition-opacity cursor-grab",
        "drag handle for reordering with a mouse",
    ),
    (
        "chat/ui/templates/chat/ui/partials/info_panel.html",
        "opacity-0 group-hover/media:opacity-100 transition-opacity truncate",
        "author caption over a shared media thumbnail; a tap opens the viewer",
    ),
]


def _sources():
    for path in WORKSPACE.rglob("*"):
        if path.suffix not in {".html", ".js"}:
            continue
        parts = set(path.relative_to(WORKSPACE).parts)
        if parts & {"vendor", "node_modules", "tests"}:
            continue
        if "templates" in parts or "static" in parts:
            yield path


def _split_variants(token):
    """``hover:none`` inside ``[@media(hover:none)]`` is not a separator."""
    parts, depth, current = [], 0, ""
    for char in token:
        if char in "[(":
            depth += 1
        elif char in "])":
            depth -= 1
        if char == ":" and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)
    return parts[:-1], parts[-1].rstrip("!")


def _hover_only(line):
    hides = reveals_on_hover = touch = False
    for token in TOKEN_SEPARATORS.split(line):
        if not token:
            continue
        variants, utility = _split_variants(token)
        if TOUCH_VARIANT in variants:
            touch = True
        elif not variants and utility in HIDING:
            hides = True
        elif any(HOVER_VARIANT.match(v) for v in variants):
            if REVEALING.fullmatch(utility):
                reveals_on_hover = True
        elif utility in HIDING:
            # A responsive or state variant (lg:opacity-0, group-data-busy:hidden)
            # hides the control in some cases too.
            hides = True
    return hides and reveals_on_hover and not touch


def _is_decorative(relative, line):
    return any(
        relative == path and needle in line for path, needle, _reason in DECORATIVE
    )


class TouchReachableActionsTests(SimpleTestCase):
    def test_hover_revealed_controls_declare_their_touch_behaviour(self):
        offenders = []
        for path in _sources():
            relative = path.relative_to(WORKSPACE).as_posix()
            text = path.read_text(encoding="utf-8")
            for number, line in enumerate(text.splitlines(), start=1):
                if _hover_only(line) and not _is_decorative(relative, line):
                    offenders.append(f"{relative}:{number}: {line.strip()}")
        if offenders:
            self.fail(
                "These controls are hidden at rest and revealed on hover only, which "
                "a touch screen never does. Add a [@media(hover:none)]: variant saying "
                "what happens there (see this test's docstring), or list a purely "
                "decorative reveal in DECORATIVE:\n" + "\n".join(offenders)
            )

    def test_decorative_entries_still_match_a_line(self):
        stale = []
        for path, needle, _reason in DECORATIVE:
            text = (WORKSPACE / path).read_text(encoding="utf-8")
            if needle not in text:
                stale.append(f"{path}: {needle}")
        self.assertEqual(stale, [], "Remove the DECORATIVE entries that match nothing.")

    def test_detection(self):
        self.assertTrue(_hover_only('class="opacity-0 group-hover:opacity-100"'))
        self.assertTrue(_hover_only("'hidden group-hover/conv:inline-flex btn'"))
        self.assertTrue(
            _hover_only('class="lg:opacity-0 lg:group-hover/att:opacity-100"')
        )
        self.assertFalse(
            _hover_only(
                'class="opacity-0 group-hover:opacity-100 [@media(hover:none)]:opacity-100"'
            )
        )
        self.assertFalse(_hover_only('class="text-xs group-hover/conv:hidden"'))
        self.assertFalse(_hover_only('class="overflow-hidden group-hover:scale-105"'))
