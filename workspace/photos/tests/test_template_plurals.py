import re
from pathlib import Path

from django.test import SimpleTestCase

TEMPLATES = Path(__file__).resolve().parent.parent / "ui" / "templates"

# A count compared to 1 to pick one of two translated strings: "1 photos" for
# a language whose singular also covers 0, or whose plural has a third form.
_HAND_BUILT = re.compile(r"\b[\w.]+\s*===?\s*1\s*\?\s*'\{\{")


class TemplatePluralsTests(SimpleTestCase):
    def test_no_template_picks_a_count_wording_by_hand(self):
        offenders = [
            f"{path.relative_to(TEMPLATES)}:{number}"
            for path in TEMPLATES.rglob("*.html")
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            )
            if _HAND_BUILT.search(line)
        ]
        self.assertEqual(
            offenders,
            [],
            "count with {% blocktranslate count %} or photosCountLabels (faces.js)",
        )
