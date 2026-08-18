from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

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
            help="Number of rows written per transaction.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        batch_size = options["batch_size"]
        if batch_size < 1:
            raise CommandError("--batch-size must be at least 1.")
        if limit is not None and limit < 0:
            raise CommandError("--limit must not be negative.")

        queryset = File.objects.filter(
            content_hash="",
            node_type=File.NodeType.FILE,
            content__isnull=False,
        ).exclude(content="")

        hashed = 0
        updated = 0
        missing = 0
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

            batch = []
            for file_obj in page:
                file_path = file_obj.content.name
                if not default_storage.exists(file_path):
                    missing += 1
                    continue

                try:
                    file_obj.content_hash = hash_storage_file(
                        default_storage, file_path
                    )
                except OSError:
                    # The blob went away between the existence check and the read.
                    missing += 1
                    continue
                hashed += 1
                if not dry_run:
                    batch.append(file_obj)

            if batch:
                updated += self._flush(batch)

        if dry_run:
            summary = f"Dry run. Would update: {hashed}, missing: {missing}."
        else:
            summary = f"Backfill complete. Updated: {updated}, missing: {missing}."
        self.stdout.write(self.style.SUCCESS(summary))

    @staticmethod
    def _flush(batch):
        """Write the hashes, skipping rows whose content changed meanwhile.

        A write through FileService between our read and this flush stored a
        fresher hash; the ``content_hash=""`` guard keeps it from being
        overwritten with the digest of bytes that no longer exist.
        """
        written = 0
        with transaction.atomic():
            for file_obj in batch:
                written += File.objects.filter(pk=file_obj.pk, content_hash="").update(
                    content_hash=file_obj.content_hash
                )
        return written
