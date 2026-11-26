"""Management command to run SQLite database maintenance."""

from django.core.management.base import BaseCommand, CommandError

from workspace.core.services.sqlite_maintenance import (
    NotEnoughSpace,
    full_vacuum,
    integrity_check,
    is_sqlite,
    run_maintenance,
)


class Command(BaseCommand):
    help = (
        "Run the SQLite maintenance pass: PRAGMA optimize, incremental vacuum "
        "and WAL checkpoint. --full-vacuum rewrites the database instead, which "
        "switches a database created before incremental auto_vacuum to it."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--full-vacuum",
            action="store_true",
            help=(
                "Rewrite the whole database with VACUUM and switch it to "
                "incremental auto_vacuum. Every write waits while it runs, and "
                "it needs about twice the database's size in free disk: it "
                "refuses to start without it."
            ),
        )
        parser.add_argument(
            "--integrity-check",
            action="store_true",
            help="Also run PRAGMA integrity_check (reads the whole database).",
        )

    def handle(self, *args, **options):
        if not is_sqlite():
            self.stdout.write(self.style.WARNING("Database backend is not SQLite."))
            return

        if options["full_vacuum"]:
            self._full_vacuum()
        else:
            self._pass()

        if options["integrity_check"]:
            self._integrity_check()
        self.stdout.write(self.style.SUCCESS("Maintenance complete."))

    def _pass(self):
        result = run_maintenance()
        self.stdout.write(f"  PRAGMA optimize: {result['optimize_ms']} ms")
        if result["auto_vacuum"] == "incremental":
            self.stdout.write(
                f"  Incremental vacuum: {result['pages_freed']} pages freed in "
                f"{result['steps']} steps, {result['freelist_count']} left "
                f"({result['incremental_vacuum_ms']} ms)"
                if result["steps"]
                else "  Incremental vacuum: no free pages"
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"  Incremental vacuum: skipped, auto_vacuum is "
                    f"{result['auto_vacuum']} ({result['freelist_count']} free "
                    "pages). Run with --full-vacuum once to switch it."
                )
            )
        self._checkpoint(result)
        self._sizes(result)

    def _full_vacuum(self):
        try:
            result = full_vacuum()
        except NotEnoughSpace as exc:
            raise CommandError(
                f"Not enough free disk for a full VACUUM: {exc}"
            ) from exc
        self.stdout.write(
            f"  VACUUM: {result['vacuum_ms']} ms, auto_vacuum "
            f"{result['auto_vacuum_before']} -> {result['auto_vacuum']}"
        )
        self._checkpoint(result)
        self._sizes(result)

    def _checkpoint(self, result):
        wal = result["wal_checkpoint"]
        self.stdout.write(
            f"  WAL checkpoint: {result['wal_checkpoint_ms']} ms "
            f"(code={wal['return_code']}, written={wal['pages_written']}, "
            f"checkpointed={wal['pages_checkpointed']})"
        )

    def _sizes(self, result):
        before, after = result["size_before"], result["size_after"]
        if before is not None and after is not None:
            self.stdout.write(
                f"  Size: {before / 2**20:.1f} MB -> {after / 2**20:.1f} MB"
            )

    def _integrity_check(self):
        result = integrity_check()
        if result["integrity_check"] == "ok":
            self.stdout.write(
                f"  Integrity check: ok ({result['integrity_check_ms']} ms)"
            )
        else:
            raise CommandError(
                f"Integrity check failed ({result['integrity_check_ms']} ms): "
                f"{result['integrity_check']}"
            )
