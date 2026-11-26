"""Management command to run SQLite database maintenance."""

from django.core.management.base import BaseCommand, CommandError

from workspace.core.services.sqlite_maintenance import (
    integrity_check,
    is_sqlite,
    run_maintenance,
)


class Command(BaseCommand):
    help = (
        "Run the SQLite maintenance pass: PRAGMA optimize, incremental vacuum "
        "and WAL checkpoint."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--integrity-check",
            action="store_true",
            help="Also run PRAGMA integrity_check (reads the whole database).",
        )

    def handle(self, *args, **options):
        if not is_sqlite():
            self.stdout.write(self.style.WARNING("Database backend is not SQLite."))
            return

        result = run_maintenance()
        self.stdout.write(f"  PRAGMA optimize: {result['optimize_ms']} ms")
        if result["auto_vacuum"] != "incremental":
            self.stdout.write(
                self.style.WARNING(
                    f"  Incremental vacuum: skipped, auto_vacuum is "
                    f"{result['auto_vacuum']}. The next migrate switches it."
                )
            )
        elif result["steps"]:
            self.stdout.write(
                f"  Incremental vacuum: {result['pages_freed']} pages freed in "
                f"{result['steps']} steps, {result['freelist_count']} left "
                f"({result['incremental_vacuum_ms']} ms)"
            )
        else:
            self.stdout.write("  Incremental vacuum: no free pages")
        wal = result["wal_checkpoint"]
        self.stdout.write(
            f"  WAL checkpoint: {result['wal_checkpoint_ms']} ms "
            f"(code={wal['return_code']}, written={wal['pages_written']}, "
            f"checkpointed={wal['pages_checkpointed']})"
        )
        before, after = result["size_before"], result["size_after"]
        if before is not None and after is not None:
            self.stdout.write(
                f"  Size: {before / 2**20:.1f} MB -> {after / 2**20:.1f} MB"
            )

        if options["integrity_check"]:
            check = integrity_check()
            if check["integrity_check"] != "ok":
                raise CommandError(
                    f"Integrity check failed: {check['integrity_check']}"
                )
            self.stdout.write(
                f"  Integrity check: ok ({check['integrity_check_ms']} ms)"
            )
        self.stdout.write(self.style.SUCCESS("Maintenance complete."))
