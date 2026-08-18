from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

from workspace.files.models import File
from workspace.files.services.content_hash import hash_storage_file


class Command(BaseCommand):
    help = (
        "Compute File.content_hash from storage for file nodes that don't have "
        "one yet (rows created before the column existed, or registered by the "
        "disk sync while their blob was unreadable)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be updated without writing changes.",
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
            help="Number of rows per bulk_update (one transaction per batch).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        batch_size = options["batch_size"]

        queryset = (
            File.objects.filter(
                content_hash="",
                node_type=File.NodeType.FILE,
                content__isnull=False,
            )
            .exclude(content="")
            .order_by("uuid")
        )
        if limit:
            queryset = queryset[:limit]

        updated = 0
        missing = 0
        to_update = []

        for file_obj in queryset.iterator():
            file_path = file_obj.content.name
            if not default_storage.exists(file_path):
                missing += 1
                continue

            file_obj.content_hash = hash_storage_file(default_storage, file_path)
            updated += 1
            if dry_run:
                continue

            to_update.append(file_obj)
            if len(to_update) >= batch_size:
                File.objects.bulk_update(to_update, ["content_hash"])
                to_update = []

        if to_update:
            File.objects.bulk_update(to_update, ["content_hash"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Backfill complete. Updated: {updated}, missing: {missing}."
            )
        )
