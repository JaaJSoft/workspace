"""Every data migration must query the connection it is migrating.

``RunPython`` hands its callable a ``schema_editor``; the ORM does not read it.
A bare ``Model.objects`` goes to the DEFAULT database, which is the right one
only while there is a single database. ``migrate_to_postgres`` migrates a
PostgreSQL target while SQLite stays the default, and there a data migration
silently reads and writes the source instead of the target.

That failure is invisible to the test suite by construction: under a single
database ``.using(default)`` IS the default, so the code is literally correct
in the environment it is tested in. Only the SQLite-to-PostgreSQL path in the
Docker workflow exercises the difference, and it does so once, at the end. This
test is the cheap half of that guard.
"""

import ast
import re
from pathlib import Path

from django.test import SimpleTestCase

WORKSPACE = Path(__file__).resolve().parent.parent.parent

# `RunPython(forwards, backwards)` - the operation whose callable owns its own
# queries. Schema operations get their connection from Django itself.
RUN_PYTHON = re.compile(r"\bRunPython\s*\(")
# Any manager access. A migration that never touches the ORM (raw SQL through
# schema_editor.execute, an index rebuild) has no connection to route.
USES_ORM = re.compile(r"\.objects\b")
ROUTES_CONNECTION = re.compile(r"connection\.alias")


def _migration_files():
    return sorted(WORKSPACE.glob("*/migrations/*.py"))


class MigrationDatabaseAliasTests(SimpleTestCase):
    def test_data_migrations_route_at_the_migrated_connection(self):
        offenders = []
        for path in _migration_files():
            source = path.read_text(encoding="utf-8")
            if not RUN_PYTHON.search(source) or not USES_ORM.search(source):
                continue
            if not ROUTES_CONNECTION.search(source):
                offenders.append(str(path.relative_to(WORKSPACE)))

        self.assertEqual(
            offenders,
            [],
            "These data migrations reach the ORM without routing at the "
            "connection being migrated. Take the alias from the schema editor "
            "(`db = schema_editor.connection.alias`) and pass it to every "
            "manager (`Model.objects.using(db)`). When the work lives in a "
            "helper, give the helper a `using` parameter rather than letting "
            "it default:\n" + "\n".join(offenders),
        )

    def test_the_guard_can_see_a_violation(self):
        """The check above only means something if it can actually fail.

        A structural test that has never been observed rejecting anything is
        indistinguishable from one whose pattern no longer matches, so the
        pattern is exercised here against a known-bad sample.
        """
        offending = (
            "from django.db import migrations\n\n\n"
            "def forwards(apps, schema_editor):\n"
            '    Thing = apps.get_model("app", "Thing")\n'
            "    Thing.objects.filter(x=1).update(y=2)\n\n\n"
            "class Migration(migrations.Migration):\n"
            "    operations = [migrations.RunPython(forwards)]\n"
        )
        self.assertTrue(RUN_PYTHON.search(offending))
        self.assertTrue(USES_ORM.search(offending))
        self.assertIsNone(ROUTES_CONNECTION.search(offending))

        compliant = offending.replace(
            "    Thing.objects.filter",
            "    db = schema_editor.connection.alias\n    Thing.objects.using(db).filter",
        )
        self.assertIsNotNone(ROUTES_CONNECTION.search(compliant))

    def test_every_migration_file_still_parses(self):
        """Cheap tripwire: the scan reads text, so it cannot see a syntax error."""
        for path in _migration_files():
            with self.subTest(migration=str(path.relative_to(WORKSPACE))):
                ast.parse(path.read_text(encoding="utf-8"))
