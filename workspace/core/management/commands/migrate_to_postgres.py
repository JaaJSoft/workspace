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
        self.stdout.write("Running pre-checks…")

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

        self.stdout.write(self.style.SUCCESS("  Source: SQLite ✓"))
        self.stdout.write(self.style.SUCCESS(f"  Target: {self._safe_url(database_url)} ✓"))

        # -- 2. Apply migrations on target ---------------------------------
        self.stdout.write("Applying migrations on target database…")
        call_command("migrate", database=TARGET_ALIAS, verbosity=0)
        self.stdout.write(self.style.SUCCESS("  Migrations applied ✓"))

        # -- 3. Export from SQLite -----------------------------------------
        self.stdout.write("Exporting data from SQLite…")

        dump_file = Path(tempfile.mktemp(suffix=".json", prefix="workspace_dump_"))
        exclude_args = []
        for app in EXCLUDED_APPS:
            exclude_args += ["--exclude", app]

        call_command(
            "dumpdata",
            "--natural-foreign",
            "--natural-primary",
            *exclude_args,
            "--indent", "2",
            "--output", str(dump_file),
            verbosity=0,
        )

        record_count = self._count_records_in_dump(dump_file)
        self.stdout.write(self.style.SUCCESS(
            f"  Exported {record_count} records to {dump_file}"
        ))

        if dry_run:
            self.stdout.write(self.style.WARNING(
                "Dry run — skipping import and verification."
            ))
            if keep_dump:
                self.stdout.write(f"  Dump kept at: {dump_file}")
            else:
                dump_file.unlink(missing_ok=True)
                self.stdout.write("  Dump file removed.")
            return

        # -- 4. Import into PostgreSQL -------------------------------------
        self.stdout.write("Importing data into PostgreSQL…")
        call_command(
            "loaddata",
            str(dump_file),
            database=TARGET_ALIAS,
            verbosity=0,
        )
        self.stdout.write(self.style.SUCCESS("  Data imported ✓"))

        # -- 5. Verify counts ----------------------------------------------
        self.stdout.write("Verifying record counts…")
        mismatches = self._verify_counts()
        if mismatches:
            self.stderr.write(self.style.ERROR("  Count mismatches found:"))
            for table, src, tgt in mismatches:
                self.stderr.write(f"    {table}: SQLite={src}, PostgreSQL={tgt}")
            self.stderr.write(self.style.ERROR(
                "Migration completed with warnings — review mismatches above."
            ))
        else:
            self.stdout.write(self.style.SUCCESS("  All table counts match ✓"))

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

    def _count_records_in_dump(self, dump_file):
        """Count total records in a dumpdata JSON file."""
        with open(dump_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return len(data)

    def _verify_counts(self):
        """Compare record counts between SQLite and PostgreSQL for all models."""
        mismatches = []
        for model in apps.get_models():
            if model._meta.proxy or model._meta.swapped:
                continue
            label = model._meta.label_lower
            try:
                src_count = model.objects.using("default").count()
                tgt_count = model.objects.using(TARGET_ALIAS).count()
            except Exception:
                continue
            if src_count != tgt_count:
                mismatches.append((label, src_count, tgt_count))
        return mismatches
