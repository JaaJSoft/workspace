from django.core.management.base import BaseCommand, CommandError

from workspace.files.models import File
from workspace.files.services.search_index import build_documents, write_documents


class Command(BaseCommand):
    help = (
        "Rebuild the full-text search index for files. The index holds only "
        "lexemes, so it cannot be rebuilt from the database alone - run this "
        "after enabling the feature on an existing install, and after changing "
        "a text extractor."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be indexed without writing changes.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Process at most N rows.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Number of rows written per transaction.",
        )
        parser.add_argument(
            "--include-trashed",
            action="store_true",
            help="Also index files sitting in the trash.",
        )
        parser.add_argument(
            "--owner",
            help="Limit to one owner, by username.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        batch_size = options["batch_size"]
        if batch_size < 1:
            raise CommandError("--batch-size must be at least 1.")
        if limit is not None and limit < 0:
            raise CommandError("--limit must not be negative.")

        queryset = File.objects.all()
        if not options["include_trashed"]:
            queryset = queryset.filter(deleted_at__isnull=True)
        if options["owner"]:
            queryset = queryset.filter(owner__username=options["owner"])

        built = 0
        indexed = 0
        seen = 0

        # Keyset pagination, one fully materialised page at a time. A
        # streaming iterator would keep a read cursor open on the connection
        # while _flush() opens a write transaction; on SQLite that fails with
        # "database is locked" (SQLITE_BUSY_SNAPSHOT, which busy_timeout does
        # not cover) as soon as the running app commits anything meanwhile.
        last_uuid = None
        while True:
            page_size = batch_size
            if limit is not None:
                page_size = min(page_size, limit - seen)
                if page_size <= 0:
                    break
            page_qs = queryset.order_by("uuid")
            if last_uuid is not None:
                page_qs = page_qs.filter(uuid__gt=last_uuid)
            page = list(page_qs[:page_size])
            if not page:
                break
            seen += len(page)
            last_uuid = page[-1].uuid

            # Blobs are read here, outside the transaction _flush opens.
            batch = build_documents(page)
            built += len(batch)
            if not dry_run and batch:
                indexed += self._flush(batch)

        if dry_run:
            summary = f"Dry run. Would index: {built}, unreadable: {seen - built}."
        else:
            summary = (
                f"Backfill complete. Indexed: {indexed}, skipped: {seen - indexed}."
            )
        self.stdout.write(self.style.SUCCESS(summary))

    @staticmethod
    def _flush(batch):
        """Write one page's documents in a single transaction."""
        return write_documents(batch)
