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
# A manager access that is NOT immediately routed. Checked per occurrence
# rather than per file: a migration that routes one query and not the next
# still writes to the default database, and that partial shape is the likeliest
# way the next mistake arrives - someone adds a query to a migration that
# already looks correct.
UNROUTED_MANAGER = re.compile(r"\.objects\b(?!\.using\()")
# Where the alias has to come from. `.using("default")` routes every manager
# and is still wrong, so the two checks are not redundant.
ROUTES_CONNECTION = re.compile(r"connection\.alias")


def _migration_files():
    return sorted(WORKSPACE.glob("*/migrations/*.py"))


def _code_lines(source):
    """Numbered lines, minus whole-line comments."""
    for number, line in enumerate(source.splitlines(), 1):
        if not line.lstrip().startswith("#"):
            yield number, line


class MigrationDatabaseAliasTests(SimpleTestCase):
    def test_every_manager_access_is_routed(self):
        offenders = []
        for path in _migration_files():
            source = path.read_text(encoding="utf-8")
            if not RUN_PYTHON.search(source):
                continue
            for number, line in _code_lines(source):
                if UNROUTED_MANAGER.search(line):
                    name = path.relative_to(WORKSPACE)
                    offenders.append(f"{name}:{number}: {line.strip()}")

        self.assertEqual(
            offenders,
            [],
            "These manager accesses in data migrations are not routed at the "
            "connection being migrated, so they query whichever database "
            "happens to be the default. Take the alias from the schema editor "
            "(`db = schema_editor.connection.alias`) and pass it to every "
            "manager (`Model.objects.using(db)`). When the work lives in a "
            "helper, give the helper a `using` parameter rather than letting "
            "it default:\n" + "\n".join(offenders),
        )

    def test_the_alias_comes_from_the_schema_editor(self):
        """Routing every manager is not enough if the alias is invented.

        ``.using("default")`` satisfies the check above while still naming a
        database the migration was not asked to touch.
        """
        offenders = []
        for path in _migration_files():
            source = path.read_text(encoding="utf-8")
            if not RUN_PYTHON.search(source) or ".objects" not in source:
                continue
            if not ROUTES_CONNECTION.search(source):
                offenders.append(str(path.relative_to(WORKSPACE)))

        self.assertEqual(
            offenders,
            [],
            "These data migrations query the ORM without taking their alias "
            "from the schema editor:\n" + "\n".join(offenders),
        )

    def test_the_guard_can_see_a_violation(self):
        """The checks above only mean something if they can actually fail.

        A structural test that has never been observed rejecting anything is
        indistinguishable from one whose pattern no longer matches, so the
        patterns are exercised here against known-bad samples.
        """
        unrouted = "    Thing.objects.filter(x=1).update(y=2)\n"
        routed = (
            "    db = schema_editor.connection.alias\n"
            "    Thing.objects.using(db).filter(x=1).update(y=2)\n"
        )

        def migration(body):
            return (
                "from django.db import migrations\n\n\n"
                "def forwards(apps, schema_editor):\n"
                '    Thing = apps.get_model("app", "Thing")\n'
                f"{body}\n\n"
                "class Migration(migrations.Migration):\n"
                "    operations = [migrations.RunPython(forwards)]\n"
            )

        # Nothing routed at all.
        self.assertTrue(UNROUTED_MANAGER.search(migration(unrouted)))
        self.assertIsNone(ROUTES_CONNECTION.search(migration(unrouted)))

        # Fully routed.
        self.assertIsNone(UNROUTED_MANAGER.search(migration(routed)))
        self.assertIsNotNone(ROUTES_CONNECTION.search(migration(routed)))

        # Routed AND unrouted in the same callable. This is the shape a
        # file-level check accepts and a per-access check rejects, and it is
        # how the next mistake most plausibly arrives: a query added to a
        # migration that already looks correct.
        mixed = migration(routed + "    Other.objects.filter(z=1).delete()\n")
        self.assertIsNotNone(
            ROUTES_CONNECTION.search(mixed),
            "the mixed sample must still satisfy the alias check, or it does "
            "not exercise the gap between the two",
        )
        self.assertTrue(UNROUTED_MANAGER.search(mixed))

    def test_every_migration_file_still_parses(self):
        """Cheap tripwire: the scan reads text, so it cannot see a syntax error."""
        for path in _migration_files():
            with self.subTest(migration=str(path.relative_to(WORKSPACE))):
                ast.parse(path.read_text(encoding="utf-8"))
