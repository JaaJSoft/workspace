from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.db.models import Q

from workspace.files.models import File


class Command(BaseCommand):
    help = (
        "Backfill File.size from storage for file nodes with missing (NULL) "
        "or zero size. Zero-size rows are re-checked because ZIP extraction "
        "used to persist size=0 for entries whose blob has real content."
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
                Q(size__isnull=True) | Q(size=0),
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
        skipped = 0
        to_update = []

        for file_obj in queryset.iterator():
            file_path = file_obj.content.name if file_obj.content else None
            if not file_path:
                skipped += 1
                continue
            if not default_storage.exists(file_path):
                missing += 1
                continue

            file_obj.size = default_storage.size(file_path)
            updated += 1
            if dry_run:
                continue

            to_update.append(file_obj)
            if len(to_update) >= batch_size:
                File.objects.bulk_update(to_update, ["size"])
                to_update = []

        if to_update:
            File.objects.bulk_update(to_update, ["size"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Backfill complete. Updated: {updated}, missing: {missing}, skipped: {skipped}."
            )
        )
