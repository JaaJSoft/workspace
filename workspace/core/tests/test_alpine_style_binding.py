"""A ``:style`` on an element ``x-show`` also hides is bound as an object.

Alpine binds a string ``:style`` by writing the whole ``style`` attribute, which
erases the ``display: none`` ``x-show`` put there. Which of the two effects runs
last decides whether the element stays hidden, so it reappears now and then,
empty, at its last coordinates. An object is applied property by property and
leaves ``display`` alone. A browser test only catches the order that happened
to run, so the rule is checked on the templates themselves.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

WORKSPACE = Path(__file__).resolve().parents[2]

TAG_START = re.compile(r"<([a-zA-Z][\w-]*)")
# An attribute value may hold its own double quotes inside {{ }} or {% %}. Each
# character can only start one branch, so a value that never closes fails in
# linear time instead of backtracking through every way to split it.
VALUE = (
    r"(?:\{\{(?:[^}]|\}(?!\}))*\}\}"
    r"|\{%(?:[^%]|%(?!\}))*%\}"
    r"|\{(?![{%])"
    r'|[^"{])*'
)
# One attribute, a bare Django tag between attributes, or the end of the tag.
TAG_PART = re.compile(
    r"\s*(?:(?P<end>/?>)"
    r"|(?P<django>\{%.*?%\}|\{\{.*?\}\}|\{#.*?#\})"
    rf'|(?P<name>[^\s=>/"]+)(?:="(?P<value>{VALUE})")?)',
    re.DOTALL,
)
HIDES = re.compile(r"x-show|x-transition(?:[.:].*)?|x-collapse(?:\..*)?")
STYLE = re.compile(r"(?::|x-bind:)style")
# An object literal, a call returning one, or a partial parameter (`*_expr`)
# that the including template writes as an object.
OBJECT_FORM = re.compile(r"\{(?!\{)|[\w.$]+\([^)]*\)$|\{\{\s*\w+_expr\s*\}\}$")


def _templates():
    for path in WORKSPACE.rglob("*.html"):
        parts = set(path.relative_to(WORKSPACE).parts)
        if "templates" in parts and not parts & {"vendor", "node_modules"}:
            yield path


def _start_tags(text):
    """``(line, {name: value})`` for every start tag in *text*."""
    for start in TAG_START.finditer(text):
        attributes = {}
        position = start.end()
        while position < len(text):
            part = TAG_PART.match(text, position)
            if part is None or part.end() == position:
                break
            position = part.end()
            if part.group("end"):
                break
            if part.group("name"):
                attributes[part.group("name")] = part.group("value") or ""
        yield text.count("\n", 0, start.start()) + 1, attributes


def string_styles_on_hidden_elements(text):
    """``(line, value)`` of each string ``:style`` on an element x-show hides."""
    for line, attributes in _start_tags(text):
        if not any(HIDES.fullmatch(name) for name in attributes):
            continue
        for name, value in attributes.items():
            if STYLE.fullmatch(name) and not OBJECT_FORM.match(value.strip()):
                yield line, value.strip()


class AlpineStyleBindingTests(SimpleTestCase):
    def test_a_style_on_an_element_x_show_hides_is_an_object(self):
        offenders = [
            f'{path.relative_to(WORKSPACE).as_posix()}:{line} :style="{value}"'
            for path in _templates()
            for line, value in string_styles_on_hidden_elements(
                path.read_text(encoding="utf-8")
            )
        ]
        self.assertEqual(
            offenders,
            [],
            "Bind :style as an object ({ left: x + 'px' }) on an element that "
            "x-show, x-transition or x-collapse also styles:\n" + "\n".join(offenders),
        )

    def test_the_scan_tells_the_two_forms_apart(self):
        string_form = (
            '<div x-show="open"\n  class="fixed"\n  :style="`left:${x}px; top:${y}px`">'
        )
        object_form = '<div x-show="open" :style="{ left: x + \'px\' }">'
        unhidden = "<div :style=\"'left:' + x + 'px'\">"

        self.assertEqual(
            list(string_styles_on_hidden_elements(string_form)),
            [(1, "`left:${x}px; top:${y}px`")],
        )
        self.assertEqual(list(string_styles_on_hidden_elements(object_form)), [])
        self.assertEqual(list(string_styles_on_hidden_elements(unhidden)), [])
