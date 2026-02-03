"""Management command to hard-delete files that exceeded trash retention."""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from workspace.files.models import File

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Permanently delete files in trash older than TRASH_RETENTION_DAYS. "
        "Physical files on disk are removed via the pre_delete signal."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=None,
            help='Override TRASH_RETENTION_DAYS (default: %(default)s).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting.',
        )

    def handle(self, *args, **options):
        retention_days = options['days'] or getattr(settings, 'TRASH_RETENTION_DAYS', 30)
        dry_run = options['dry_run']
        cutoff = timezone.now() - timedelta(days=retention_days)

        qs = File.objects.filter(deleted_at__lte=cutoff)
        files_count = qs.filter(node_type=File.NodeType.FILE).count()
        folders_count = qs.filter(node_type=File.NodeType.FOLDER).count()
        total = files_count + folders_count

        if not total:
            self.stdout.write("Nothing to purge.")
            return

        label = "Would delete" if dry_run else "Deleting"
        self.stdout.write(
            f"{label} {files_count} files and {folders_count} folders "
            f"trashed more than {retention_days} days ago…"
        )

        if dry_run:
            return

        qs.delete()
        self.stdout.write(self.style.SUCCESS(
            f"Purged {files_count} files and {folders_count} folders."
        ))
