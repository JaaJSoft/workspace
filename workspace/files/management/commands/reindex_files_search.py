from django.core.management.base import BaseCommand

from workspace.files.models import File
from workspace.files.services.search_index import index_file


class Command(BaseCommand):
    help = (
        "Rebuild the full-text search index for files. The index holds only "
        "lexemes, so it cannot be rebuilt from the database alone - run this "
        "after enabling the feature on an existing install, after changing a "
        "text extractor, and (on SQLite) after any migration that rewrote the "
        "files_file table, which reassigns the rowids the index is keyed on."
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

        indexed = failed = 0
        for file_obj in qs.iterator(chunk_size=options["batch_size"]):
            if index_file(file_obj):
                indexed += 1
            else:
                failed += 1
            if (indexed + failed) % 1000 == 0:
                self.stdout.write(f"  {indexed + failed} files processed...")

        self.stdout.write(
            self.style.SUCCESS(f"Indexed {indexed} files ({failed} failed).")
        )
        return None
