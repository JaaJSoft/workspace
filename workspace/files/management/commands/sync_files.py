"""Management command to sync files between disk storage and database."""

import logging

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from workspace.files.models import File
from workspace.files.services import disk_sync

User = get_user_model()
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Synchronize files between disk storage and database. "
        "Adds missing disk files to DB and soft-deletes orphaned DB entries."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be changed without writing.",
        )
        parser.add_argument(
            "--user",
            type=str,
            default=None,
            help="Sync only the specified username. Default: all users.",
        )
        parser.add_argument(
            "--folder",
            type=str,
            default=None,
            help="UUID of a specific folder to sync (shallow). Requires --user.",
        )
        parser.add_argument(
            "--shallow",
            action="store_true",
            help="Only sync immediate children (not recursive).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        username = options["user"]
        folder_uuid = options["folder"]
        shallow = options["shallow"]

        if folder_uuid:
            if not username:
                self.stderr.write(
                    self.style.ERROR("--folder requires --user to be specified.")
                )
                return

            user = User.objects.get(username=username)
            folder_db = File.objects.get(
                uuid=folder_uuid,
                owner=user,
                node_type=File.NodeType.FOLDER,
                deleted_at__isnull=True,
            )
            try:
                result = disk_sync.sync_folder_level(
                    user, folder_db, dry_run=dry_run, log=logger
                )
            except disk_sync.NotPersonalFolder as refusal:
                self.stderr.write(self.style.ERROR(str(refusal)))
                return
            self._print_result(result, f"folder '{folder_db.name}'")
            return

        if username:
            users = User.objects.filter(username=username)
        else:
            users = User.objects.filter(is_active=True)

        for user in users:
            self.stdout.write(f"Syncing user: {user.username}")
            if shallow:
                result = disk_sync.sync_folder_level(user, dry_run=dry_run, log=logger)
            else:
                result = disk_sync.sync_user_tree(user, dry_run=dry_run, log=logger)
            self._print_result(result, f"user '{user.username}'")

    def _print_result(self, result, scope):
        self.stdout.write(
            self.style.SUCCESS(
                f"Sync complete for {scope}: "
                + ", ".join(f"{name}={n}" for name, n in result.counts().items())
            )
        )
        if result.errors:
            for err in result.errors:
                self.stderr.write(self.style.WARNING(f"  ERROR: {err}"))
