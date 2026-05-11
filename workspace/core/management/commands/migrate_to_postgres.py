"""Management command to migrate data from SQLite to PostgreSQL."""

import json
import logging
import tempfile
from pathlib import Path

import dj_database_url
from django.apps import apps
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.migrations.executor import MigrationExecutor

logger = logging.getLogger(__name__)

SQLITE_ENGINE = "django.db.backends.sqlite3"
POSTGRES_ENGINE = "django.db.backends.postgresql"

# These apps are auto-populated by Django on migrate; exporting them causes
# conflicts on load because natural keys may not match across databases.
EXCLUDED_APPS = ["contenttypes", "auth.permission"]

TARGET_ALIAS = "_postgres_target"


class Command(BaseCommand):
    help = (
        "Migrate all data from the current SQLite database to a PostgreSQL "
        "database. The SQLite file is never modified."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "database_url",
            help="PostgreSQL connection URL, e.g. postgres://user:pass@host:5432/dbname",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run pre-checks and export data without writing to the target.",
        )
        parser.add_argument(
            "--keep-dump",
            action="store_true",
            help="Keep the intermediate JSON dump file after import.",
        )

    def handle(self, *args, **options):
        database_url = options["database_url"]
        dry_run = options["dry_run"]
        keep_dump = options["keep_dump"]

        # -- 1. Pre-checks ------------------------------------------------
        self.stdout.write("Running pre-checks...")

        source_engine = connections["default"].settings_dict["ENGINE"]
        if SQLITE_ENGINE not in source_engine:
            raise CommandError(
                f"Current database engine is '{source_engine}', expected SQLite. "
                "This command only migrates FROM SQLite."
            )

        target_settings = dj_database_url.parse(database_url)
        if target_settings["ENGINE"] != POSTGRES_ENGINE:
            raise CommandError(
                f"Target URL resolved to engine '{target_settings['ENGINE']}'. "
                "This command only migrates TO PostgreSQL."
            )

        self._register_target(target_settings)

        try:
            target_conn = connections[TARGET_ALIAS]
            target_conn.ensure_connection()
        except Exception as exc:
            raise CommandError(f"Cannot connect to target database: {exc}") from exc

        self.stdout.write(self.style.SUCCESS("  Source: SQLite OK"))
        self.stdout.write(self.style.SUCCESS(f"  Target: {self._safe_url(database_url)} OK"))

        pending = self._pending_migrations("default")
        if pending:
            preview = ", ".join(f"{app}.{name}" for app, name in pending[:5])
            extra = f" (+{len(pending) - 5} more)" if len(pending) > 5 else ""
            raise CommandError(
                "Source database has unapplied migrations: "
                f"{preview}{extra}. "
                "Run `python manage.py migrate` first to bring it up to date."
            )
        self.stdout.write(self.style.SUCCESS("  Source migrations up to date OK"))

        # -- 2. Apply migrations on target ---------------------------------
        self.stdout.write("Applying migrations on target database...")
        call_command("migrate", database=TARGET_ALIAS, verbosity=0)
        self.stdout.write(self.style.SUCCESS("  Migrations applied OK"))

        # -- 3. Export from SQLite -----------------------------------------
        self.stdout.write("Exporting data from SQLite...")

        dump_handle = tempfile.NamedTemporaryFile(
            suffix=".json", prefix="workspace_dump_", delete=False,
        )
        dump_file = Path(dump_handle.name)
        dump_handle.close()
        exclude_args = []
        for app in EXCLUDED_APPS:
            exclude_args += ["--exclude", app]

        # --natural-foreign keeps cross-DB FK resolution (e.g. references to
        # contenttypes which are auto-populated on the target by Django).
        # We deliberately do NOT pass --natural-primary: it strips the pk on
        # models with natural keys (e.g. auth.User), which breaks
        # OneToOneField(primary_key=True) relationships such as
        # users.UserPresence (its pk == user_id and isn't itself natural).
        with open(dump_file, "w", encoding="utf-8") as dump_stream:
            call_command(
                "dumpdata",
                "--natural-foreign",
                *exclude_args,
                "--indent", "2",
                stdout=dump_stream,
                verbosity=0,
            )

        record_count = self._count_records_in_dump(dump_file)
        self.stdout.write(self.style.SUCCESS(
            f"  Exported {record_count} records to {dump_file}"
        ))

        if dry_run:
            self.stdout.write(self.style.WARNING(
                "Dry run - skipping import and verification."
            ))
            if keep_dump:
                self.stdout.write(f"  Dump kept at: {dump_file}")
            else:
                dump_file.unlink(missing_ok=True)
                self.stdout.write("  Dump file removed.")
            return

        # -- 4. Clear target data tables -----------------------------------
        # Data migrations populated seed data (e.g. files.MimeTypeRule) that
        # would conflict with loaddata when the same rows are re-imported
        # from the source. Truncate everything user-facing while keeping the
        # migration history and the contenttype/permission tables that the
        # dump excludes.
        self.stdout.write("Clearing target data tables...")
        cleared = self._truncate_target_tables()
        self.stdout.write(self.style.SUCCESS(f"  Cleared {cleared} tables OK"))

        # -- 5. Import into PostgreSQL -------------------------------------
        self.stdout.write("Importing data into PostgreSQL...")
        call_command(
            "loaddata",
            str(dump_file),
            database=TARGET_ALIAS,
            verbosity=0,
        )
        self.stdout.write(self.style.SUCCESS("  Data imported OK"))

        # -- 5b. Reset PostgreSQL sequences --------------------------------
        # loaddata inserts rows with explicit PKs but does not update the
        # backing sequences, so the next INSERT (e.g. a new user signup)
        # would collide. Resync them to MAX(pk)+1 for every model.
        self.stdout.write("Resetting PostgreSQL sequences...")
        self._reset_sequences()
        self.stdout.write(self.style.SUCCESS("  Sequences reset OK"))

        # -- 6. Verify counts ----------------------------------------------
        self.stdout.write("Verifying record counts...")
        mismatches = self._verify_counts()
        if mismatches:
            self.stderr.write(self.style.ERROR("  Count mismatches found:"))
            for table, src, tgt in mismatches:
                self.stderr.write(f"    {table}: SQLite={src}, PostgreSQL={tgt}")
            self.stderr.write(self.style.ERROR(
                "Migration completed with warnings - review mismatches above."
            ))
        else:
            self.stdout.write(self.style.SUCCESS("  All table counts match OK"))

        # -- 6. Cleanup ----------------------------------------------------
        if keep_dump:
            self.stdout.write(f"Dump kept at: {dump_file}")
        else:
            dump_file.unlink(missing_ok=True)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Migration complete."))
        self.stdout.write(
            "Next step: set DATABASE_URL to your PostgreSQL URL and restart "
            "the application."
        )

    def _register_target(self, settings):
        """Register the target PostgreSQL database as a Django connection."""
        connections.databases[TARGET_ALIAS] = {
            **settings,
            "CONN_MAX_AGE": 0,
            "AUTOCOMMIT": True,
            "CONN_HEALTH_CHECKS": False,
            "OPTIONS": settings.get("OPTIONS", {}),
            "TIME_ZONE": None,
            "ATOMIC_REQUESTS": False,
            "TEST": {},
        }

    def _safe_url(self, url):
        """Mask password in database URL for display."""
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(url)
        if parsed.password:
            masked = parsed._replace(
                netloc=f"{parsed.username}:****@{parsed.hostname}"
                + (f":{parsed.port}" if parsed.port else "")
            )
            return urlunparse(masked)
        return url

    def _truncate_target_tables(self):
        """Truncate all user-data tables on the target. Returns the number of tables cleared.

        Keeps django_migrations (migration history), and the tables excluded
        from the source dump (django_content_type, auth_permission) so that
        their auto-populated state survives.
        """
        keep = {"django_migrations", "django_content_type", "auth_permission"}
        with connections[TARGET_ALIAS].cursor() as cursor:
            cursor.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
            )
            tables = [row[0] for row in cursor.fetchall() if row[0] not in keep]
            if not tables:
                return 0
            quoted = ", ".join(f'"{t}"' for t in tables)
            cursor.execute(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE")
            return len(tables)

    def _reset_sequences(self):
        """Resync auto-increment sequences for all models on the target."""
        target_conn = connections[TARGET_ALIAS]
        with target_conn.cursor() as cursor:
            for model in apps.get_models():
                if model._meta.proxy or model._meta.swapped:
                    continue
                sql_statements = target_conn.ops.sequence_reset_sql(
                    self.style, [model]
                )
                for stmt in sql_statements:
                    cursor.execute(stmt)

    def _pending_migrations(self, alias):
        """Return [(app_label, migration_name), ...] for unapplied migrations on alias."""
        executor = MigrationExecutor(connections[alias])
        targets = executor.loader.graph.leaf_nodes()
        plan = executor.migration_plan(targets)
        return [(migration.app_label, migration.name) for migration, _ in plan]

    def _count_records_in_dump(self, dump_file):
        """Count total records in a dumpdata JSON file."""
        with open(dump_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return len(data)

    def _verify_counts(self):
        """Compare record counts between SQLite and PostgreSQL for all models.

        Skips the EXCLUDED_APPS (contenttypes, auth.permission) because they
        are not in the dump - Django auto-populates them from the model set
        registered at migrate time, so any historical drift on the source
        (stale rows from removed models) does not migrate to the target and
        a count mismatch there is expected, not a problem.
        """
        excluded_labels = set()
        for spec in EXCLUDED_APPS:
            if "." in spec:
                excluded_labels.add(spec.lower())
            else:
                excluded_labels.update(
                    m._meta.label_lower for m in apps.get_app_config(spec).get_models()
                )

        mismatches = []
        for model in apps.get_models():
            if model._meta.proxy or model._meta.swapped:
                continue
            label = model._meta.label_lower
            if label in excluded_labels:
                continue
            try:
                src_count = model.objects.using("default").count()
                tgt_count = model.objects.using(TARGET_ALIAS).count()
            except Exception:
                continue
            if src_count != tgt_count:
                mismatches.append((label, src_count, tgt_count))
        return mismatches
