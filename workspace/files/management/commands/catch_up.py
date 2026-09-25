"""Run the file readers' catch-up by hand: backfills, or a forced reanalysis.

Idempotent: a file whose derived data matches its current content is not
pending, so a second run finds nothing left to do.
"""

from django.core.management.base import BaseCommand, CommandError

from workspace.files.services.catch_up import (
    pending_ids,
    queue_pending,
    registered_catch_ups,
    resolve,
)


class Command(BaseCommand):
    help = "Queue the files whose derived data (media info, photo library...) is missing or stale."

    def add_arguments(self, parser):
        parser.add_argument(
            "readers",
            nargs="*",
            help="Readers to run (default: all of them).",
        )
        parser.add_argument(
            "--reanalyze",
            action="store_true",
            help="Process every file a reader reads, not only those missing or stale.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Stop after this many files per reader.",
        )
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Run each file inline instead of queueing it.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many files would be processed, and queue nothing.",
        )

    def handle(self, *args, **options):
        from workspace.files.tasks import catch_up_file

        readers, unknown = resolve(options["readers"])
        if unknown:
            known = ", ".join(sorted(r.name for r in registered_catch_ups()))
            raise CommandError(
                f"Unknown reader(s): {', '.join(unknown)}. Known: {known}."
            )
        reanalyze = options["reanalyze"]
        limit = options["limit"]
        for reader in readers:
            if not reader.enabled():
                self.stdout.write(f"{reader.name}: disabled on this deployment.")
                continue
            if options["dry_run"]:
                total = reader.pending_files(reanalyze=reanalyze).count()
                if limit is not None:
                    total = min(total, limit)
                self.stdout.write(f"{reader.name}: would process {total} file(s).")
            elif options["sync"]:
                processed = 0
                for uuid in pending_ids(reader, reanalyze=reanalyze, limit=limit):
                    catch_up_file(reader.name, str(uuid), reanalyze=reanalyze)
                    processed += 1
                self.stdout.write(
                    self.style.SUCCESS(f"{reader.name}: processed {processed} file(s).")
                )
            else:
                queued = queue_pending(reader, reanalyze=reanalyze, limit=limit)
                self.stdout.write(
                    self.style.SUCCESS(f"{reader.name}: queued {queued} file(s).")
                )
