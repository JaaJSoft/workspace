"""Run the file readers' catch-up by hand: backfills, or a forced reanalysis.

Idempotent: a file whose derived data matches its current content is not
pending, so a second run finds nothing left to do.
"""

from django.core.management.base import BaseCommand, CommandError

from workspace.common.task_priority import BACKGROUND_PRIORITY
from workspace.files.services.catch_up import (
    get_catch_up,
    pending_ids,
    registered_catch_ups,
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

        readers = self._readers(options["readers"])
        reanalyze = options["reanalyze"]
        limit = options["limit"]
        for reader in readers:
            if options["dry_run"]:
                total = reader.pending(reanalyze=reanalyze).count()
                if limit is not None:
                    total = min(total, limit)
                self.stdout.write(f"{reader.name}: would process {total} file(s).")
                continue

            dispatched = 0
            for uuid in pending_ids(reader, reanalyze=reanalyze, limit=limit):
                if options["sync"]:
                    catch_up_file(reader.name, str(uuid), reanalyze=reanalyze)
                else:
                    catch_up_file.apply_async(
                        args=[reader.name, str(uuid)],
                        kwargs={"reanalyze": reanalyze},
                        priority=BACKGROUND_PRIORITY,
                    )
                dispatched += 1
            verb = "processed" if options["sync"] else "queued"
            self.stdout.write(
                self.style.SUCCESS(f"{reader.name}: {verb} {dispatched} file(s).")
            )

    def _readers(self, names):
        if not names:
            return registered_catch_ups()
        unknown = [name for name in names if get_catch_up(name) is None]
        if unknown:
            known = ", ".join(sorted(r.name for r in registered_catch_ups()))
            raise CommandError(
                f"Unknown reader(s): {', '.join(unknown)}. Known: {known}."
            )
        return [get_catch_up(name) for name in dict.fromkeys(names)]
