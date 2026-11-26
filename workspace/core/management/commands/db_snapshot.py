"""Management command to write a consistent copy of the SQLite database."""

from django.core.management.base import BaseCommand, CommandError

from workspace.core.services.sqlite_maintenance import (
    NotEnoughSpace,
    is_sqlite,
    snapshot,
)


class Command(BaseCommand):
    help = (
        "Write a compacted, consistent copy of the SQLite database to PATH "
        "with VACUUM INTO. Writers keep going while it runs. The copy holds "
        "the database only, not the uploaded files."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", help="Where to write the copy; must not exist.")

    def handle(self, *args, **options):
        if not is_sqlite():
            raise CommandError("Database backend is not SQLite.")
        try:
            result = snapshot(options["path"])
        except FileExistsError as exc:
            raise CommandError(f"{exc} already exists.") from exc
        except FileNotFoundError as exc:
            raise CommandError(f"No such directory: {exc}") from exc
        except NotEnoughSpace as exc:
            raise CommandError(f"Not enough free disk for the snapshot: {exc}") from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Snapshot written to {result['path']} "
                f"({result['size'] / 2**20:.1f} MB, {result['snapshot_ms']} ms)"
            )
        )
