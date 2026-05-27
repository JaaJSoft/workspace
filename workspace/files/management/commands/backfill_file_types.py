import logging

from django.core.management.base import BaseCommand
from django.db import models

from workspace.files.models import File

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Backfill File.type and File.category for files that have no type set."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        from workspace.files.services.detection import detect_from_bytes, detect_from_name

        batch_size = options["batch_size"]
        dry_run = options["dry_run"]

        pks = list(
            File.objects.filter(
                node_type=File.NodeType.FILE,
            ).filter(
                models.Q(type='unknown') | models.Q(category='unknown')
            )
            .order_by("pk")
            .values_list("pk", flat=True)
        )

        total = len(pks)
        self.stdout.write(f"Found {total} files to backfill.")

        updated = 0
        errors = 0

        for i in range(0, total, batch_size):
            batch_pks = pks[i : i + batch_size]
            batch = list(File.objects.filter(pk__in=batch_pks))

            to_update = []
            for file_obj in batch:
                try:
                    if file_obj.content and file_obj.content.name:
                        try:
                            content = file_obj.content.read()
                            detection = detect_from_bytes(content)
                        except (FileNotFoundError, OSError):
                            detection = detect_from_name(file_obj.name)
                        finally:
                            file_obj.content.close()
                    else:
                        detection = detect_from_name(file_obj.name)

                    file_obj.type = detection.label
                    file_obj.category = detection.group or 'unknown'
                    to_update.append(file_obj)
                except Exception as e:
                    errors += 1
                    logger.warning("Failed to detect %s: %s", file_obj.uuid, e)
                    file_obj.type = "unknown"
                    file_obj.category = "unknown"
                    to_update.append(file_obj)

            if not dry_run and to_update:
                File.objects.bulk_update(to_update, ["type", "category"])
                updated += len(to_update)

            processed = min(i + batch_size, total)
            self.stdout.write(f"  Processed {processed}/{total} ({errors} errors)")

        action = "Would update" if dry_run else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} {updated} files. {errors} errors."))
