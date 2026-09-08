"""A block element's closing tag sits at the indent of its opening tag.

When a tag carries enough attributes to be spread over several lines, djLint
has to put the content somewhere. For an **inline** element it welds the
content to the brackets::

    <span
      class="text-xs text-base-content/50"
    >{{ conv.time_ago }}</span>

which looks wrong but is the only correct rendering: a line break inside an
inline element is collapsible whitespace that the browser *renders*, so moving
the content to its own line would widen the element by a space on each side and
shift whatever sits beside it. Chromium measures the welded form at exactly the
width of the single-line form, and the "aligned" form a space wider on each
side.

A **block** element has no such constraint - css drops the whitespace against
its edges - so there the same shape is pure noise, and the content belongs on
its own line::

    <h2
      class="text-2xl font-bold mb-2"
    >
      Welcome
    </h2>

djLint has no rule for this and its formatter produced 42 of them the day the
project moved to 1.46.1, so the check lives here.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

WORKSPACE = Path(__file__).resolve().parents[2]

# Elements whose content is laid out as a block, so whitespace against their
# inner edges is dropped rather than rendered. Deliberately conservative:
# table cells, <pre>, <textarea> and every inline element are left out, because
# there a line break is content.
BLOCK_ELEMENTS = frozenset(
    "article aside blockquote dd details dialog div dl dt fieldset figcaption "
    "figure footer form h1 h2 h3 h4 h5 h6 header li main nav ol section "
    "summary ul".split()
)

# The line that closes a tag spread over several lines, and whatever the
# author welded to it. An empty rest is the shape we want.
BRACKET_LINE = re.compile(r"^(\s*)>(.*)$")
OPENING_TAG = re.compile(r"^\s*<([a-z][a-z0-9]*)\b")


def _templates():
    for path in WORKSPACE.rglob("*.html"):
        parts = set(path.relative_to(WORKSPACE).parts)
        if "templates" not in parts:
            continue
        if parts & {"vendor", "tests", "node_modules"}:
            continue
        yield path


def _element_opened_above(lines, index, indent):
    """Name of the element whose opening tag ends on the bracket line `index`.

    djLint indents the bracket to match the ``<tag`` that opened it and its
    attributes one level deeper, so walking back over the deeper lines lands
    on the opening tag itself.
    """
    for line in reversed(lines[:index]):
        if not line.strip():
            return None
        depth = len(line) - len(line.lstrip())
        if depth > indent:
            continue
        match = OPENING_TAG.match(line)
        if match and depth == indent:
            return match.group(1)
        return None
    return None


class BlockClosingTagAlignmentTests(SimpleTestCase):
    def test_no_block_element_welds_content_to_its_bracket(self):
        """Every block element gives its content a line of its own."""
        offenders = []
        for path in _templates():
            lines = path.read_text().splitlines()
            for number, line in enumerate(lines, 1):
                bracket = BRACKET_LINE.match(line)
                if bracket is None:
                    continue
                welded = bracket.group(2).strip()
                if not welded:
                    continue
                element = _element_opened_above(
                    lines, number - 1, len(bracket.group(1))
                )
                if element is None or element not in BLOCK_ELEMENTS:
                    continue
                # An element with no content at all closes on the bracket
                # line and has nothing to move down.
                if welded == f"</{element}>":
                    continue
                relative = path.relative_to(WORKSPACE)
                offenders.append(f"{relative}:{number}: {line.strip()}")

        self.assertEqual(
            offenders,
            [],
            "A block element must give its content a line of its own so the "
            "closing tag lands at the indent of the opening tag:\n"
            + "\n".join(offenders),
        )
