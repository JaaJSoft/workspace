"""Guards the settings package layout.

``workspace/settings/`` splits the configuration across topic modules that
``__init__`` star-imports. Adding a module without wiring it in is silent:
Django simply never sees the settings it defines, and the feature falls back
to whatever default the code uses. These tests fail instead.
"""

import ast
import importlib
import pkgutil
import unittest
from pathlib import Path

from django.conf import settings

import workspace.settings


def _submodules():
    for info in pkgutil.iter_modules(workspace.settings.__path__):
        yield importlib.import_module(f"workspace.settings.{info.name}")


def _assigned_settings(module):
    """Uppercase names assigned at any depth in the module's own source."""
    tree = ast.parse(Path(module.__file__).read_text())
    names = set()
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id.isupper():
                names.add(target.id)
    return {name for name in names if not name.startswith("_")}


class SettingsPackageTests(unittest.TestCase):
    def test_every_module_setting_reaches_django_settings(self):
        # Reachability only: the test runner overrides some values (DEBUG,
        # ALLOWED_HOSTS), so comparing against the module attribute would fail
        # for reasons that have nothing to do with the package layout.
        for module in _submodules():
            # Intersect with the module namespace so settings defined inside a
            # conditional branch that did not run are not expected.
            for name in sorted(_assigned_settings(module) & set(vars(module))):
                with self.subTest(module=module.__name__, setting=name):
                    self.assertTrue(
                        hasattr(settings, name),
                        f"{name} is defined in {module.__name__} but never reaches "
                        f"django.conf.settings - add the module to "
                        f"workspace/settings/__init__.py",
                    )

    def test_no_setting_is_defined_twice(self):
        owners = {}
        for module in _submodules():
            for name in _assigned_settings(module):
                owners.setdefault(name, []).append(module.__name__)

        duplicates = {
            name: modules for name, modules in owners.items() if len(modules) > 1
        }
        self.assertEqual(
            duplicates,
            {},
            "a setting must be defined in exactly one settings module, "
            "otherwise the value depends on import order",
        )
