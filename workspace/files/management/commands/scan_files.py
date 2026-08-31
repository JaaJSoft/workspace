"""Retroactively scan an existing library for malware.

Deliberately not part of the upload path: a backfill over a large library is
a maintenance operation, not something a user's upload should trigger.
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from workspace.files.models import File

# Rows fetched per keyset page. A read cursor held open across the write
# transactions the tasks perform is what makes SQLite raise "database is
# locked", so the selection is paged rather than streamed with .iterator().
_PAGE_SIZE = 500


class Command(BaseCommand):
    help = "Queue malware scans for files that have never been scanned."

    def add_arguments(self, parser):
        parser.add_argument(
            "--rescan",
            action="store_true",
            help="Scan every file, not only those without a verdict.",
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
            help="Run each scan inline instead of queueing it.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many files would be scanned, and queue nothing.",
        )

    def handle(self, *args, **options):
        from workspace.files.tasks import scan_file

        if not getattr(settings, "FILES_MALWARE_SCAN_ENABLED", False):
            self.stdout.write(
                self.style.WARNING(
                    "Malware scanning is disabled "
                    "(set FILES_MALWARE_SCAN_ENABLED=1). Nothing to do."
                )
            )
            return

        # Both exclusions are needed. Django renders exclude(content="") as
        # NOT (content = '' AND content IS NOT NULL), so a NULL content makes
        # the inner AND false and the row survives - it would then consume a
        # --limit slot only for scan_file to return "not_applicable".
        queryset = (
            File.objects.filter(
                node_type=File.NodeType.FILE,
                deleted_at__isnull=True,
            )
            .exclude(content="")
            .exclude(content__isnull=True)
        )
        if not options["rescan"]:
            queryset = queryset.filter(scan__isnull=True)

        limit = options["limit"]
        if options["dry_run"]:
            total = queryset.count() if limit is None else min(queryset.count(), limit)
            self.stdout.write(f"Would scan {total} file(s).")
            return

        dispatched = 0
        last_uuid = None
        while True:
            page_qs = queryset.order_by("uuid")
            if last_uuid is not None:
                page_qs = page_qs.filter(uuid__gt=last_uuid)
            page = list(page_qs.values_list("uuid", flat=True)[:_PAGE_SIZE])
            if not page:
                break
            last_uuid = page[-1]
            for uuid in page:
                if limit is not None and dispatched >= limit:
                    break
                if options["sync"]:
                    scan_file(str(uuid))
                else:
                    scan_file.delay(str(uuid))
                dispatched += 1
            if limit is not None and dispatched >= limit:
                break

        verb = "Scanned" if options["sync"] else "Queued"
        self.stdout.write(self.style.SUCCESS(f"{verb} {dispatched} file(s)."))
