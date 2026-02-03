"""Management command to run SQLite database maintenance."""

import logging

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Run SQLite maintenance: PRAGMA optimize, WAL checkpoint, "
        "VACUUM, and integrity check."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-vacuum',
            action='store_true',
            help='Skip VACUUM (can be slow on large databases).',
        )
        parser.add_argument(
            '--skip-integrity-check',
            action='store_true',
            help='Skip PRAGMA integrity_check.',
        )

    def handle(self, *args, **options):
        from workspace.core.tasks import _run_maintenance

        self.stdout.write("Starting SQLite maintenance…")

        result = _run_maintenance(
            skip_vacuum=options['skip_vacuum'],
            skip_integrity_check=options['skip_integrity_check'],
        )

        if result.get('skipped'):
            self.stdout.write(self.style.WARNING(result['reason']))
            return

        # optimize
        self.stdout.write(self.style.SUCCESS(
            f"  PRAGMA optimize: {result['optimize_ms']} ms"
        ))

        # WAL checkpoint
        wal = result['wal_checkpoint']
        self.stdout.write(self.style.SUCCESS(
            f"  WAL checkpoint: {result['wal_checkpoint_ms']} ms "
            f"(code={wal['return_code']}, written={wal['pages_written']}, "
            f"checkpointed={wal['pages_checkpointed']})"
        ))

        # VACUUM
        if result.get('vacuum_skipped'):
            self.stdout.write(self.style.WARNING("  VACUUM: skipped"))
        else:
            size_info = ""
            if result.get('size_before') is not None:
                before_kb = result['size_before'] / 1024
                after_kb = result['size_after'] / 1024
                saved_kb = result.get('size_saved', 0) / 1024
                size_info = (
                    f" (before={before_kb:.1f} KB, after={after_kb:.1f} KB, "
                    f"saved={saved_kb:.1f} KB)"
                )
            self.stdout.write(self.style.SUCCESS(
                f"  VACUUM: {result['vacuum_ms']} ms{size_info}"
            ))

        # Integrity check
        if result.get('integrity_check_skipped'):
            self.stdout.write(self.style.WARNING("  Integrity check: skipped"))
        else:
            check = result['integrity_check']
            if check == 'ok':
                self.stdout.write(self.style.SUCCESS(
                    f"  Integrity check: ok ({result['integrity_check_ms']} ms)"
                ))
            else:
                self.stderr.write(self.style.ERROR(
                    f"  Integrity check FAILED ({result['integrity_check_ms']} ms): {check}"
                ))

        self.stdout.write(self.style.SUCCESS("Maintenance complete."))
