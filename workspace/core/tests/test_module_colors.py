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
