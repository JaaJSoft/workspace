from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.db import transaction

from workspace.files.models import File


class Command(BaseCommand):
    help = "Backfill File.size from storage for file nodes with missing size."

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

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]

        queryset = (
            File.objects.filter(
                node_type=File.NodeType.FILE,
                size__isnull=True,
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

        for file_obj in queryset.iterator():
            file_path = file_obj.content.name if file_obj.content else None
            if not file_path:
                skipped += 1
                continue
            if not default_storage.exists(file_path):
                missing += 1
                continue

            size = default_storage.size(file_path)
            if dry_run:
                updated += 1
                continue

            with transaction.atomic():
                File.objects.filter(pk=file_obj.pk).update(size=size)
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Backfill complete. Updated: {updated}, missing: {missing}, skipped: {skipped}."
            )
        )
