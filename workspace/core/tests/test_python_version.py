"""Keeps the interpreter a checkout is built against declared in one place.

`uv` accepts a pre-release of the release named by the lower bound of
`requires-python`, so `>=3.14` and `>=3.14.0` both let 3.14.0rc2 build the
venv - and on a release candidate no `manage.py` command runs at all: the first
pydantic model raises a bare `AssertionError` from a `typing._eval_type`
keyword only the final release has. The floor therefore names a *patch*
release, which uv does refuse to satisfy with a candidate.

Nothing at runtime checks that the rest of the tree agrees with that floor, and
the drift is invisible from CI - `actions/setup-python` resolves a final
release whatever the workflows ask for, so only the developer's machine breaks.
"""

import re
import tomllib
import unittest

from django.conf import settings

PYPROJECT = settings.BASE_DIR / "pyproject.toml"
LOCK = settings.BASE_DIR / "uv.lock"
MANAGE = settings.BASE_DIR / "manage.py"
WORKFLOWS = settings.BASE_DIR / ".github" / "workflows"
PYTHON_VERSION_FILE = settings.BASE_DIR / ".python-version"

FLOOR_RE = re.compile(r">=\s*(?P<version>\d+(?:\.\d+)*)")
MINIMUM_RE = re.compile(r"^MINIMUM_PYTHON = \((?P<parts>[\d,\s]+)\)$", re.M)
SETUP_PYTHON_RE = re.compile(r"^\s*python-version: '(?P<version>\d+(?:\.\d+)*)'$", re.M)


def _version(text):
    return tuple(int(part) for part in text.strip().split("."))


def _floor(declared):
    """The `>=` bound of a `requires-python` specifier, as a tuple of ints."""
    return _version(FLOOR_RE.search(declared)["version"])


def _pyproject_floor():
    return _floor(tomllib.loads(PYPROJECT.read_text())["project"]["requires-python"])


def _lock_floor():
    return _floor(tomllib.loads(LOCK.read_text())["requires-python"])


def _manage_minimum():
    return tuple(
        int(part) for part in MINIMUM_RE.search(MANAGE.read_text())["parts"].split(",")
    )


def _setup_python_versions():
    """Every version `actions/setup-python` is handed, by workflow file name."""
    return {
        workflow.name: [
            _version(m["version"])
            for m in SETUP_PYTHON_RE.finditer(workflow.read_text())
        ]
        for workflow in sorted(WORKFLOWS.glob("*.yml"))
    }


class RequiresPythonTests(unittest.TestCase):
    def test_floor_names_a_patch_release(self):
        floor = _pyproject_floor()
        self.assertEqual(
            len(floor),
            3,
            "requires-python must name major.minor.patch - a two-part bound admits a release candidate",
        )
        self.assertGreater(
            floor[2],
            0,
            f"requires-python >={'.'.join(map(str, floor))} still admits {floor[0]}.{floor[1]}.0rcN - "
            "raise the floor to the first patch release of that minor",
        )

    def test_lockfile_matches_pyproject(self):
        self.assertEqual(
            _lock_floor(),
            _pyproject_floor(),
            "run `uv lock` - uv.lock was resolved against a different Python floor",
        )


class DeclaredFloorTests(unittest.TestCase):
    def setUp(self):
        self.floor = _pyproject_floor()

    def test_manage_py_check_matches_the_floor(self):
        self.assertEqual(
            _manage_minimum(),
            self.floor,
            "update MINIMUM_PYTHON in manage.py - its early check no longer matches requires-python",
        )

    def test_workflows_run_the_declared_minor(self):
        versions = _setup_python_versions()
        self.assertTrue(
            any(found for found in versions.values()),
            "no workflow passes a python-version to actions/setup-python any more",
        )
        for workflow, found in versions.items():
            for version in found:
                self.assertEqual(
                    version[:2],
                    self.floor[:2],
                    f"{workflow} tests a Python minor the project does not declare - "
                    "align it with requires-python in pyproject.toml",
                )
                if len(version) == 3:
                    self.assertGreaterEqual(
                        version,
                        self.floor,
                        f"{workflow} pins a Python patch release below requires-python",
                    )

    @unittest.skipUnless(PYTHON_VERSION_FILE.exists(), "no .python-version in the tree")
    def test_python_version_file_matches_the_floor(self):
        pinned = _version(PYTHON_VERSION_FILE.read_text())
        self.assertEqual(
            pinned[:2],
            self.floor[:2],
            ".python-version pins a Python minor the project does not declare",
        )
        if len(pinned) == 3:
            self.assertGreaterEqual(
                pinned,
                self.floor,
                ".python-version pins a patch release below requires-python",
            )
