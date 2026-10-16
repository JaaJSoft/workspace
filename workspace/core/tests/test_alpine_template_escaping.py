"""Django values inside Alpine expressions must stay inside their JS string.

Alpine evaluates its attributes as JavaScript, and the browser decodes HTML
entities before Alpine reads them: the ``&#x27;`` Django's autoescape writes
for a quote is a quote again by the time the expression runs. A value quoted
as ``'{{ value }}'`` can therefore close its string and append code, and
autoescape cannot see it - ``|escapejs`` is what makes it a JS-safe literal.

The check is deliberately blind to what the value is. A UUID cannot break out
today, but the template does not know it is a UUID, and the next field
interpolated the same way may be free text (a reaction's emoji was). One
filter on every quoted value costs nothing and leaves nothing to judge.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

WORKSPACE = Path(__file__).resolve().parents[2]

# Attributes whose value Alpine evaluates as an expression. x-ref, x-cloak,
# x-transition, x-collapse and alpine-ajax's x-target/x-merge take names or
# modifiers, not code.
EVALUATED = (
    r"x-(?:data|init|show|if|for|text|html|model|modelable|effect|id|trap"
    r"|on:[\w.:-]+|bind:[\w.:-]+)"
    r"|@[\w.:-]+"
    r"|:[\w.:-]+"
)
# A tag or an interpolation may carry its own double quotes ({{ x|default:"a" }})
# without ending the attribute.
ATTRIBUTE = re.compile(rf'\s({EVALUATED})="((?:\{{\{{.*?\}}\}}|\{{%.*?%\}}|[^"])*)"')
DJANGO_TAG = re.compile(r"\{%.*?%\}|\{#.*?#\}")
INTERPOLATION = re.compile(r"\{\{(.*?)\}\}")
# A quote the browser hands to Alpine: a literal one, or the entities that
# decode to one. ``\'`` is an escaped quote inside a JS string, not a delimiter.
SINGLE_QUOTE = re.compile(r"(?<!\\)'|&#x27;|&#39;|&apos;")


def _templates():
    for path in WORKSPACE.rglob("*.html"):
        parts = set(path.relative_to(WORKSPACE).parts)
        if "templates" in parts and not parts & {"vendor", "node_modules"}:
            yield path


def _blank(match):
    return " " * len(match.group(0))


def _unescaped(text):
    """``(line, attribute, interpolation, problem)`` for each unsafe value in *text*."""
    for attribute in ATTRIBUTE.finditer(text):
        # Quotes inside tags and comments ({% url 'name' %}) are not JS.
        value = DJANGO_TAG.sub(_blank, attribute.group(2))
        # Quotes inside an interpolation (|date:'c') are filter arguments.
        code = INTERPOLATION.sub(_blank, value)
        line = text.count("\n", 0, attribute.start()) + 1
        for interpolation in INTERPOLATION.finditer(value):
            before = code[: interpolation.start()]
            if before.count("`") % 2:
                problem = "sits in a template literal"
            elif len(SINGLE_QUOTE.findall(before)) % 2 and (
                "escapejs" not in interpolation.group(1)
            ):
                problem = "is quoted without |escapejs"
            else:
                continue
            yield line, attribute.group(1), interpolation.group(0), problem


class AlpineTemplateEscapingTests(SimpleTestCase):
    def test_quoted_values_in_alpine_expressions_are_escaped(self):
        offenders = [
            f"{path.relative_to(WORKSPACE).as_posix()}:{line} {attr} {expr} {problem}"
            for path in _templates()
            for line, attr, expr, problem in _unescaped(
                path.read_text(encoding="utf-8")
            )
        ]
        self.assertEqual(
            offenders,
            [],
            "quote the value as '{{ value|escapejs }}', or pass it through "
            "|json_script - see 'Django values inside Alpine expressions'",
        )


class UnescapedDetectorTests(SimpleTestCase):
    def _problems(self, html):
        return [problem for *_, problem in _unescaped(html)]

    def test_quoted_value_without_escapejs_is_flagged(self):
        self.assertEqual(
            self._problems("<b @click=\"pick('{{ g.emoji }}')\">"),
            ["is quoted without |escapejs"],
        )

    def test_escaped_unquoted_and_non_alpine_values_pass(self):
        html = (
            "<b x-data=\"app('{{ name|escapejs }}', {{ count }})\" "
            'title="\'{{ name }}\'" x-target="{{ target }}">'
        )
        self.assertEqual(self._problems(html), [])

    def test_quotes_inside_django_tags_are_not_delimiters(self):
        html = "<b @click=\"go('{% url 'a:b' %}', {{ count }})\">"
        self.assertEqual(self._problems(html), [])

    def test_quotes_in_filter_arguments_are_not_delimiters(self):
        html = "<b x-data=\"app({{ a|default:'x' }}, '{{ b|escapejs }}')\">"
        self.assertEqual(self._problems(html), [])

    def test_double_quotes_inside_an_interpolation_do_not_end_the_attribute(self):
        self.assertEqual(
            self._problems('<b x-data="{ tab: \'{{ tab|default:"all" }}\' }">'),
            ["is quoted without |escapejs"],
        )

    def test_entity_quote_opens_a_string(self):
        self.assertEqual(
            self._problems('<b x-show="a == &#x27;{{ v }}&#x27;">'),
            ["is quoted without |escapejs"],
        )

    def test_template_literal_is_flagged_even_when_escaped(self):
        # |escapejs leaves ${ alone, so it cannot make a template literal safe.
        self.assertEqual(
            self._problems('<b @click="go(`/x/{{ v|escapejs }}`)">'),
            ["sits in a template literal"],
        )
