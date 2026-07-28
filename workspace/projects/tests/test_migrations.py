import os
import subprocess
import sys
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[3]

# Replicates the migrate_to_postgres step that broke: migrating a freshly
# registered non-default alias while the default connection already sits on
# the final schema.
MIGRATE_TARGET_ALIAS = """
import sys

import django

django.setup()
from django.core.management import call_command
from django.db import connections

connections.databases["target"] = {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": sys.argv[1],
    "ATOMIC_REQUESTS": False,
    "AUTOCOMMIT": True,
    "CONN_MAX_AGE": 0,
    "CONN_HEALTH_CHECKS": False,
    "OPTIONS": {},
    "TIME_ZONE": None,
    "TEST": {},
}
call_command("migrate", "projects", database="target", verbosity=0)
"""


class ProjectGroupsMigrationTests(SimpleTestCase):
    """The 0008 data migration must query the connection being migrated.

    migrate_to_postgres migrates a second alias while the default
    connection already sits on the final schema (no ``group_id`` column).
    A RunPython that reads through the default connection instead of
    ``schema_editor.connection.alias`` crashes there with "no such column:
    projects_project.group_id". Django's test runner refuses runtime
    database aliases, so the scenario runs in subprocesses like the real
    command does.
    """

    def test_data_migration_targets_the_migrated_connection(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            source = Path(tmp) / "source.sqlite3"
            target = Path(tmp) / "target.sqlite3"
            env = {
                **os.environ,
                "DJANGO_SETTINGS_MODULE": "workspace.settings",
                "DATABASE_URL": f"sqlite:///{source.as_posix()}",
            }
            first = subprocess.run(
                [sys.executable, "manage.py", "migrate", "projects", "-v", "0"],
                env=env,
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            second = subprocess.run(
                [sys.executable, "-c", MIGRATE_TARGET_ALIAS, str(target)],
                env=env,
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
