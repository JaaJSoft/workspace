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
#
# Deliberately stricter than correctness requires. A queryset built only to be
# wrapped in Subquery() or Exists() is compiled against the OUTER query's
# connection, so routing it changes nothing - but telling that apart from a
# real query needs statement-level analysis (the OuterRef marking it as
# correlated is usually on a different line from the `.objects`), and the
# routing is a harmless no-op there. One rule that is occasionally redundant
# beats a clever one that is occasionally wrong.
UNROUTED_MANAGER = re.compile(r"\.objects\b(?!\.using\()")
# Where the alias has to come from. `.using("default")` routes every manager
# and is still wrong, so the two checks are not redundant.
ROUTES_CONNECTION = re.compile(r"connection\.alias")
# A migration whose queries live one call away. Three of them do this, passing
# historical models to a service function; their own text holds no `.objects`,
# so the per-access check is blind to them and only this one can see them.
# Narrowed to `services` on purpose: migrations also import pure helpers
# (`common.logging.scrub`, a key generator), and those have nothing to route.
DELEGATES_TO_SERVICE = re.compile(r"^from workspace\.\w+\.services", re.M)
# `.using("default")` - an alias the migration named itself rather than took
# from the connection it was handed. It satisfies the per-access check while
# pointing at a database nobody asked for, and it is the one wrong-alias shape
# a text scan can recognise: a literal. Passing the wrong *variable* would need
# dataflow analysis, which this deliberately does not attempt.
LITERAL_ALIAS = re.compile(r"\.using\(\s*['\"]")


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

        This also covers the shape the per-access check cannot see: a
        migration whose queries live one call away, in a service it imports.
        Its own text holds no ``.objects`` at all, so it has to be caught by
        what it delegates to rather than by what it contains.
        """
        offenders = []
        for path in _migration_files():
            source = path.read_text(encoding="utf-8")
            if not RUN_PYTHON.search(source):
                continue
            if not DELEGATES_TO_SERVICE.search(source) and ".objects" not in source:
                continue
            if not ROUTES_CONNECTION.search(source):
                offenders.append(str(path.relative_to(WORKSPACE)))

        self.assertEqual(
            offenders,
            [],
            "These data migrations reach the ORM - directly, or through a "
            "service they import - without taking their alias from the schema "
            "editor. A delegating migration has to pass it on: give the "
            "helper a `using` parameter, the way it already takes its models "
            "as arguments:\n" + "\n".join(offenders),
        )

    def test_no_migration_names_a_database_itself(self):
        """An alias the migration wrote down is not the one it was handed.

        ``.using("default")`` routes every manager and satisfies both checks
        above while naming a database nobody asked for - the migration would
        keep writing to the source even when the target is something else.
        Only a literal is detectable this way; passing the wrong variable
        would need dataflow analysis, and this does not attempt it.
        """
        offenders = []
        for path in _migration_files():
            source = path.read_text(encoding="utf-8")
            if not RUN_PYTHON.search(source):
                continue
            for number, line in _code_lines(source):
                if LITERAL_ALIAS.search(line):
                    name = path.relative_to(WORKSPACE)
                    offenders.append(f"{name}:{number}: {line.strip()}")

        self.assertEqual(
            offenders,
            [],
            "These migrations route at a database they named themselves "
            "instead of the one the schema editor handed them:\n"
            + "\n".join(offenders),
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

        # An alias the migration named itself, next to a correct one it never
        # uses. Every manager is routed and `connection.alias` is present, so
        # both checks above accept it; only the literal check rejects it.
        invented = migration(
            "    db = schema_editor.connection.alias\n"
            '    Thing.objects.using("default").filter(x=1).update(y=2)\n'
        )
        self.assertIsNone(UNROUTED_MANAGER.search(invented))
        self.assertIsNotNone(ROUTES_CONNECTION.search(invented))
        self.assertTrue(LITERAL_ALIAS.search(invented))
        self.assertIsNone(LITERAL_ALIAS.search(migration(routed)))

    def test_every_migration_file_still_parses(self):
        """Cheap tripwire: the scan reads text, so it cannot see a syntax error."""
        for path in _migration_files():
            with self.subTest(migration=str(path.relative_to(WORKSPACE))):
                ast.parse(path.read_text(encoding="utf-8"))
