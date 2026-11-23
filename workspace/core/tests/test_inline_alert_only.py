"""Templates show a message box through ``<inline-alert>``, never daisyUI's ``alert``.

A hand-written ``alert alert-error`` renders as a saturated block that looks
nothing like the bordered box every other dialog shows, and it drifts further
with each copy (``text-white`` here, a ``x-circle`` icon there). Field hints
under an input and per-row errors in a list stay plain text: only the
``alert`` component class is refused.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

WORKSPACE = Path(__file__).resolve().parents[2]

# The class attribute, static or bound, on one line or spread over several.
CLASS_ATTRIBUTE = re.compile(r'(?<![\w-])(?::|x-bind:)?class="([^"]*)"', re.DOTALL)
ALERT_CLASS = re.compile(
    r"(?<![\w:/-])alert(?:-(?:info|success|warning|error|sm))?(?![\w-])"
)


def _templates():
    for path in WORKSPACE.rglob("*.html"):
        parts = set(path.relative_to(WORKSPACE).parts)
        if "templates" in parts and not parts & {"vendor", "node_modules"}:
            yield path


def alert_classes(text):
    """``(line, class value)`` of each class attribute naming daisyUI's alert."""
    for attribute in CLASS_ATTRIBUTE.finditer(text):
        if ALERT_CLASS.search(attribute.group(1)):
            line = text.count("\n", 0, attribute.start()) + 1
            yield line, " ".join(attribute.group(1).split())


class InlineAlertOnlyTests(SimpleTestCase):
    def test_no_template_uses_the_daisyui_alert_class(self):
        offenders = [
            f'{path.relative_to(WORKSPACE).as_posix()}:{line} class="{value}"'
            for path in _templates()
            for line, value in alert_classes(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual(
            offenders,
            [],
            "Use <inline-alert> (workspace/common/static/ui/js/inline_alert.js) "
            "instead of daisyUI's alert class.",
        )

    def test_the_check_sees_an_alert_in_every_form_it_takes(self):
        self.assertEqual(
            [
                line
                for line, _ in alert_classes(
                    '<div class="alert alert-error text-white">'
                )
            ],
            [1],
        )
        self.assertEqual(
            [line for line, _ in alert_classes('<p>\n<div\n  class="mt-2\n alert"\n>')],
            [3],
        )
        self.assertEqual(
            [
                line
                for line, _ in alert_classes(
                    "<div :class=\"ok ? 'alert-success' : ''\">"
                )
            ],
            [1],
        )

    def test_the_check_ignores_names_that_only_contain_alert(self):
        markup = (
            '<i data-lucide="triangle-alert" class="lucide-alert"></i>'
            '<div class="alert-banner my-alert"></div>'
            '<span class="text-error" role="alert"></span>'
        )
        self.assertEqual(list(alert_classes(markup)), [])
