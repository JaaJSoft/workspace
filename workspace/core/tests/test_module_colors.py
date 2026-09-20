"""Module colors: a fixed Tailwind hue per module, shared by Python and the stylesheet."""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from workspace.core.module_registry import MODULE_HUES, ModuleInfo, ModuleRegistry

CONFIG = Path(settings.BASE_DIR) / "scripts" / "frontend" / "tailwind.config.js"
STYLESHEET = (
    Path(settings.BASE_DIR) / "workspace" / "common" / "static" / "css" / "app.css"
)

HUES_BLOCK_RE = re.compile(r"const MODULE_HUES = \{(?P<body>.*?)\n\};", re.S)
HUE_LINE_RE = re.compile(r"^\s*(?P<hue>[a-z]+):\s*\{", re.M)


def _make_module(color):
    return ModuleInfo(
        name="X", slug="x", description="", icon="i", color=color, url="/x"
    )


class ModuleHueValidationTests(SimpleTestCase):
    def test_register_rejects_a_daisy_slot(self):
        reg = ModuleRegistry()
        with self.assertRaises(ValueError):
            reg.register(_make_module("primary"))

    def test_register_accepts_every_known_hue(self):
        reg = ModuleRegistry()
        for i, hue in enumerate(MODULE_HUES):
            reg.register(
                ModuleInfo(
                    name=hue, slug=f"m{i}", description="", icon="i", color=hue, url="/"
                )
            )
        self.assertEqual(len(reg.get_all()), len(MODULE_HUES))

    def test_every_registered_module_uses_a_known_hue(self):
        from workspace.core.module_registry import registry

        for module in registry.get_all():
            self.assertIn(module.color, MODULE_HUES, module.slug)


MODULE_COMPONENTS = (
    "btn-module",
    "badge-module",
    "toggle-module",
    "link-module",
    "progress-module",
    "range-module",
    "radio-module",
    "checkbox-module",
    "input-module",
)


class ModuleHueStylesheetTests(SimpleTestCase):
    def test_tailwind_config_lists_the_same_hues(self):
        body = HUES_BLOCK_RE.search(CONFIG.read_text(encoding="utf-8"))
        self.assertIsNotNone(body, "MODULE_HUES table missing from tailwind.config.js")
        config_hues = HUE_LINE_RE.findall(body["body"])
        self.assertEqual(sorted(config_hues), sorted(MODULE_HUES))

    def test_compiled_bundle_has_a_rule_per_hue(self):
        css = STYLESHEET.read_text(encoding="utf-8")
        missing = [hue for hue in MODULE_HUES if f".module-{hue}{{" not in css]
        self.assertEqual(missing, [], "run `npm run build:css` in scripts/frontend")

    def test_compiled_bundle_has_the_module_components(self):
        css = STYLESHEET.read_text(encoding="utf-8")
        missing = [
            cls
            for cls in MODULE_COMPONENTS
            if not re.search(rf"\.{re.escape(cls)}[{{:\[]", css)
        ]
        self.assertEqual(missing, [], "run `npm run build:css` in scripts/frontend")

    def test_compiled_bundle_has_the_module_utilities(self):
        css = STYLESHEET.read_text(encoding="utf-8")
        for cls in (r".text-module{", r".bg-module\/10{", r".text-module-content{"):
            self.assertIn(cls, css)
