from django.core.management.base import BaseCommand

from workspace.files.models import File
from workspace.files.services.search_index import index_file


class Command(BaseCommand):
    help = (
        "Rebuild the full-text search index for files. The index holds only "
        "lexemes, so it cannot be rebuilt from the database alone - run this "
        "after enabling the feature on an existing install, and after changing "
        "a text extractor."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--include-trashed",
            action="store_true",
            help="Also index files sitting in the trash.",
        )
        parser.add_argument(
            "--owner",
            help="Limit to one owner, by username.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Rows fetched per query (default: 500).",
        )

    def handle(self, *args, **options):
        qs = File.objects.all()
        if not options["include_trashed"]:
            qs = qs.filter(deleted_at__isnull=True)
        if options["owner"]:
            qs = qs.filter(owner__username=options["owner"])

        indexed = failed = reported = 0
        # Keyset pagination, one fully materialised page at a time. A
        # streaming iterator would keep a read cursor open on the connection
        # while each index_file() opens a write transaction; on SQLite that
        # can fail with "database is locked" (SQLITE_BUSY_SNAPSHOT, which
        # busy_timeout does not cover) once the running app commits meanwhile.
        # Same reason as backfill_file_hashes.
        last_uuid = None
        while True:
            page_qs = qs.order_by("uuid")
            if last_uuid is not None:
                page_qs = page_qs.filter(uuid__gt=last_uuid)
            page = list(page_qs[: options["batch_size"]])
            if not page:
                break
            last_uuid = page[-1].uuid

            for file_obj in page:
                if index_file(file_obj):
                    indexed += 1
                else:
                    failed += 1
            if (indexed + failed) - reported >= 1000:
                reported = indexed + failed
                self.stdout.write(f"  {reported} files processed...")

        self.stdout.write(
            self.style.SUCCESS(f"Indexed {indexed} files ({failed} failed).")
        )
        return None
