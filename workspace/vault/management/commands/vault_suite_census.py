import json

from django.core.management.base import BaseCommand

from workspace.vault.services.census import UNREADABLE_ID, census
from workspace.vault.services.suites import state


class Command(BaseCommand):
    help = "Count stored vault rows per crypto algorithm id. Counts only, no user data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--json", action="store_true", help="Machine-readable output."
        )
        parser.add_argument("--database", default="default")

    def handle(self, *args, **options):
        rows = [
            {
                "axis": axis,
                "id": identifier,
                "count": count,
                "state": "unreadable"
                if identifier == UNREADABLE_ID
                else state(axis, identifier) or "absent",
            }
            for (axis, identifier), count in sorted(
                census(options["database"]).items(), key=str
            )
        ]
        if options["json"]:
            self.stdout.write(json.dumps(rows))
            return
        for row in rows:
            self.stdout.write(
                f"{row['axis']} {row['id']}: {row['count']} ({row['state']})"
            )
