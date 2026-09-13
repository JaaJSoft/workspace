import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

# Tailwind's content scanner never reads .py files, so a `fill-*`/`stroke-*`
# class built as a plain string in a chart service only exists in the
# compiled bundle because scripts/frontend/tailwind.config.js safelists it.
# This test closes the loop: every such literal found in the services must
# resolve to an actual rule in the built CSS.
_CLASS_RE = re.compile(r"\b(?:fill|stroke)-[a-z][a-z0-9-]*(?:/\d+)?\b")


def _chart_service_files():
    root = Path(settings.BASE_DIR) / "workspace"
    yield root / "common" / "charts.py"
    yield from root.glob("*/services/*.py")


class ChartClassesCompiledTests(SimpleTestCase):
    def test_every_chart_css_class_is_in_the_compiled_bundle(self):
        css_path = Path(settings.BASE_DIR) / "workspace/common/static/css/app.css"
        css = css_path.read_text(encoding="utf-8")

        classes = set()
        for path in _chart_service_files():
            if not path.is_file():
                continue
            classes.update(_CLASS_RE.findall(path.read_text(encoding="utf-8")))

        self.assertTrue(classes, "expected at least one fill-/stroke- class literal")
        missing = [
            cls for cls in sorted(classes) if f".{cls.replace('/', '\\/')}" not in css
        ]
        self.assertEqual(
            missing,
            [],
            f"classes missing from the compiled bundle (extend the tailwind.config.js "
            f"safelist and run npm run build:css): {missing}",
        )
