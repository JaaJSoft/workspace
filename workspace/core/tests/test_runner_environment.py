"""What the test runner switches on must stay inside the test process.

Tests start real subprocesses (``manage.py migrate`` against a second alias,
a gunicorn boot). Those inherit ``os.environ``, not the runner's settings
overrides, so a switch the runner exports there reaches them without the
marker that makes it legitimate.
"""

import os
import subprocess
import sys

from django.conf import settings
from django.test import SimpleTestCase


class SubprocessEnvironmentTests(SimpleTestCase):
    def test_a_subprocess_started_by_a_test_passes_the_system_checks(self):
        result = subprocess.run(
            [sys.executable, "manage.py", "check"],
            env={**os.environ, "DJANGO_SETTINGS_MODULE": "workspace.settings"},
            cwd=settings.BASE_DIR,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_vault_test_suites_are_on_in_the_test_process(self):
        self.assertTrue(settings.VAULT_TEST_SUITES)
        self.assertTrue(settings.VAULT_TEST_RUNNER_ACTIVE)
