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

from workspace.test_runner import GUARDED_LIFECYCLE_MODELS, armed_row_write_guards


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


class RowWriteGuardTests(SimpleTestCase):
    """The full-row-write guard is a pre_save receiver, and a receiver lives in
    the process that connected it. Under ``--parallel`` the tests run in
    workers that the default forkserver start method builds from a fresh
    import, so a guard armed only in the parent is silently off there."""

    def test_the_guard_is_armed_in_the_process_running_the_tests(self):
        self.assertEqual(
            armed_row_write_guards(), {"files.File", *GUARDED_LIFECYCLE_MODELS}
        )
