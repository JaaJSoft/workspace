"""Guards the CI workflow against drifting from the repository it tests.

The E2E jobs run inside the official Playwright image instead of installing
Chromium on every run. That image only contains the browser build matching
its own tag, and the ``playwright`` Python package only drives the build it
was released with - so the image tag and the locked package version have to
move together. Nothing at runtime checks that: a mismatch surfaces as
"Executable doesn't exist" once the tests are already running.

The E2E and JS jobs also enumerate their modules by hand, so a module whose
tests land after the matrix was written is never run - and the workflow stays
green while doing it. Those lists are checked against the tree here.
"""

import re
import tomllib
import unittest

from django.conf import settings

WORKFLOW = settings.BASE_DIR / ".github" / "workflows" / "tests.yml"
LOCK = settings.BASE_DIR / "uv.lock"

IMAGE_RE = re.compile(
    r"image:\s*mcr\.microsoft\.com/playwright/python:v(?P<version>[\d.]+)-\w+"
)
JOB_RE = re.compile(r"^  (?P<name>[\w-]+):\n(?P<body>(?:(?:    .*)?\n)*)", re.M)
MATRIX_MODULES_RE = re.compile(r"^ +module:\n(?P<items>(?:^ +- [\w.-]+\n)+)", re.M)


def _locked_playwright_version():
    lock = tomllib.loads(LOCK.read_text())
    return next(
        pkg["version"] for pkg in lock["package"] if pkg["name"] == "playwright"
    )


def _matrix_modules(job):
    """The `module:` list of a workflow job, as a set."""
    body = next(
        m["body"] for m in JOB_RE.finditer(WORKFLOW.read_text()) if m["name"] == job
    )
    items = MATRIX_MODULES_RE.search(body)["items"]
    return {line.strip(" -") for line in items.splitlines()}


def _modules_with_tests(subdir, pattern):
    """Modules holding at least one `tests/<subdir>/` file matching `pattern`."""
    root = settings.BASE_DIR / "workspace"
    return {
        module.name
        for module in root.iterdir()
        if module.is_dir() and any((module / "tests" / subdir).rglob(pattern))
    }


class PlaywrightImageTests(unittest.TestCase):
    def test_e2e_image_matches_locked_playwright_version(self):
        matches = IMAGE_RE.findall(WORKFLOW.read_text())
        self.assertTrue(matches, "the E2E job no longer runs in the Playwright image")
        for image_version in matches:
            self.assertEqual(
                image_version,
                _locked_playwright_version(),
                "bump the Playwright image tag in tests.yml to the version pinned in uv.lock",
            )


class MatrixCoverageTests(unittest.TestCase):
    def test_js_matrix_lists_every_module_with_js_tests(self):
        self.assertEqual(
            _matrix_modules("js"),
            _modules_with_tests("js", "*.test.js"),
            "update the `js` matrix in tests.yml - a module's JS tests are unrun or its job is empty",
        )

    def test_e2e_matrix_lists_every_module_with_e2e_tests(self):
        self.assertEqual(
            _matrix_modules("e2e"),
            _modules_with_tests("e2e", "test_*.py"),
            "update the `e2e` matrix in tests.yml - a module's E2E tests are unrun or its job is empty",
        )
