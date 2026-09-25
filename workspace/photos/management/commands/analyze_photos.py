"""Fill the photo library for photos and videos uploaded before it read them.

Idempotent: a file whose row matches its current content is not pending, so a
second run finds nothing left to do.
"""

from django.core.management.base import BaseCommand

from workspace.photos.services.analysis import pending_media_ids, pending_media_qs


class Command(BaseCommand):
    help = "Queue the analysis of photos and videos whose MediaItem row is missing or stale."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reanalyze",
            action="store_true",
            help="Analyze every photo and video, not only those missing or stale.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Stop after this many files.",
        )
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Run each analysis inline instead of queueing it.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many files would be analyzed, and queue nothing.",
        )

    def handle(self, *args, **options):
        from workspace.photos.tasks import analyze_photo

        limit = options["limit"]
        if options["dry_run"]:
            total = pending_media_qs(reanalyze=options["reanalyze"]).count()
            if limit is not None:
                total = min(total, limit)
            self.stdout.write(f"Would analyze {total} file(s).")
            return

        dispatched = 0
        for uuid in pending_media_ids(reanalyze=options["reanalyze"], limit=limit):
            if options["sync"]:
                analyze_photo(str(uuid))
            else:
                analyze_photo.delay(str(uuid))
            dispatched += 1

        verb = "Analyzed" if options["sync"] else "Queued"
        self.stdout.write(self.style.SUCCESS(f"{verb} {dispatched} file(s)."))
