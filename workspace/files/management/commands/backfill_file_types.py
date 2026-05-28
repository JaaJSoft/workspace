import logging

from django.core.management.base import BaseCommand
from django.db import models

from workspace.common.logging import scrub
from workspace.files.models import File

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Backfill type and category for File and MessageAttachment rows still set to 'unknown'."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        dry_run = options["dry_run"]

        self._backfill_files(batch_size, dry_run)
        self._backfill_attachments(batch_size, dry_run)

    def _backfill_files(self, batch_size, dry_run):
        from workspace.files.services.detection import detect_from_bytes, detect_from_name

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
        self.stdout.write(f"[File] Found {total} files to backfill.")

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
                    logger.warning("Failed to detect %s: %s", scrub(str(file_obj.uuid)), scrub(str(e)))
                    file_obj.type = "unknown"
                    file_obj.category = "unknown"
                    to_update.append(file_obj)

            if to_update:
                updated += len(to_update)
                if not dry_run:
                    File.objects.bulk_update(to_update, ["type", "category"])

            processed = min(i + batch_size, total)
            self.stdout.write(f"  Processed {processed}/{total} ({errors} errors)")

        action = "Would update" if dry_run else "Updated"
        self.stdout.write(self.style.SUCCESS(f"[File] {action} {updated} files. {errors} errors."))

    def _backfill_attachments(self, batch_size, dry_run):
        from workspace.chat.models import MessageAttachment
        from workspace.files.services.detection import detect_from_bytes, detect_from_name

        pks = list(
            MessageAttachment.objects.filter(
                models.Q(type='unknown') | models.Q(category='unknown')
            )
            .order_by("pk")
            .values_list("pk", flat=True)
        )

        total = len(pks)
        self.stdout.write(f"[MessageAttachment] Found {total} attachments to backfill.")

        updated = 0
        errors = 0

        for i in range(0, total, batch_size):
            batch_pks = pks[i : i + batch_size]
            batch = list(MessageAttachment.objects.filter(pk__in=batch_pks))

            to_update = []
            for att in batch:
                try:
                    if att.file and att.file.name:
                        try:
                            content = att.file.read()
                            detection = detect_from_bytes(content)
                        except (FileNotFoundError, OSError):
                            detection = detect_from_name(att.original_name)
                        finally:
                            att.file.close()
                    else:
                        detection = detect_from_name(att.original_name)

                    att.type = detection.label
                    att.category = detection.group or 'unknown'
                    to_update.append(att)
                except Exception as e:
                    errors += 1
                    logger.warning("Failed to detect attachment %s: %s", scrub(str(att.uuid)), scrub(str(e)))
                    att.type = "unknown"
                    att.category = "unknown"
                    to_update.append(att)

            if to_update:
                updated += len(to_update)
                if not dry_run:
                    MessageAttachment.objects.bulk_update(to_update, ["type", "category"])

            processed = min(i + batch_size, total)
            self.stdout.write(f"  Processed {processed}/{total} ({errors} errors)")

        action = "Would update" if dry_run else "Updated"
        self.stdout.write(self.style.SUCCESS(
            f"[MessageAttachment] {action} {updated} attachments. {errors} errors."
        ))
