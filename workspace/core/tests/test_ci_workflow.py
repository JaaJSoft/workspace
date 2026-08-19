"""Guards the CI workflow against drifting from the locked dependencies.

The E2E jobs run inside the official Playwright image instead of installing
Chromium on every run. That image only contains the browser build matching
its own tag, and the ``playwright`` Python package only drives the build it
was released with - so the image tag and the locked package version have to
move together. Nothing at runtime checks that: a mismatch surfaces as
"Executable doesn't exist" once the tests are already running.
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


def _locked_playwright_version():
    lock = tomllib.loads(LOCK.read_text())
    return next(
        pkg["version"] for pkg in lock["package"] if pkg["name"] == "playwright"
    )


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
