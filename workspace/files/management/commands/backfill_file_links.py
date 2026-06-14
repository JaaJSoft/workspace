"""Backfill FileLink rows from existing markdown content.

Idempotent: reconciliation converges to the same edge set, so re-running is
safe. The event handler keeps links current going forward; this command is the
one-time catch-up for notes that already contain wikilinks.
"""

from django.core.management.base import BaseCommand

from workspace.files.models import File, FileLink
from workspace.files.services.links import reconcile_file_links


class Command(BaseCommand):
    help = "Backfill note-link (FileLink) rows from existing markdown content."

    def handle(self, *args, **options):
        qs = File.objects.filter(
            node_type=File.NodeType.FILE,
            type="markdown",
            deleted_at__isnull=True,
        )
        scanned = 0
        for file in qs.iterator():
            reconcile_file_links(file)
            scanned += 1
        total = FileLink.objects.count()
        self.stdout.write(
            self.style.SUCCESS(
                f"Scanned {scanned} markdown file(s); {total} link(s) total."
            )
        )
