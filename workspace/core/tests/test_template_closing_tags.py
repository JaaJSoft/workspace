"""A block element's closing tag sits at the indent of its opening tag.

When a tag carries enough attributes to be spread over several lines, djLint
has to put the content somewhere. For an **inline** element it keeps the
content welded to the brackets::

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
its edges - so there the same shape is pure noise, and the closing tag belongs
at the indent of the opening tag::

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
    (
        "article", "aside", "blockquote", "dd", "details", "dialog", "div",
        "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form",
        "h1", "h2", "h3", "h4", "h5", "h6", "header", "li", "main", "nav",
        "ol", "section", "summary", "ul",
    )
)

# A line that opens with the closing bracket of a tag spread over several
# lines, with the content welded to it rather than given its own line.
WELDED_CONTENT = re.compile(r"^\s*>(?=[^\s<])")
FIRST_CLOSING_TAG = re.compile(r"</([a-z0-9]+)>")


def _templates():
    for path in WORKSPACE.rglob("*.html"):
        parts = set(path.relative_to(WORKSPACE).parts)
        if "templates" not in parts:
            continue
        if parts & {"vendor", "tests", "node_modules"}:
            continue
        yield path


class BlockClosingTagAlignmentTests(SimpleTestCase):
    def test_no_block_element_welds_its_content_to_the_bracket(self):
        offenders = []
        for path in _templates():
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if not WELDED_CONTENT.match(line):
                    continue
                closing = FIRST_CLOSING_TAG.search(line)
                if closing is None or closing.group(1) not in BLOCK_ELEMENTS:
                    continue
                relative = path.relative_to(WORKSPACE)
                offenders.append(f"{relative}:{number}: {line.strip()}")

        self.assertEqual(
            offenders,
            [],
            "A block element must close at the indent of its opening tag; give "
            "its content a line of its own:\n" + "\n".join(offenders),
        )
