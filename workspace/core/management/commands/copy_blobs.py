"""Copy every blob of the instance from one storage backend to another.

The layout is the same on both sides - a bucket mirrors what MEDIA_ROOT
holds - so moving an instance between them is a copy, not a transformation.
Only the directories the app stores blobs under are copied: MEDIA_ROOT can
also hold the SQLite database and the face detection weights, which are not
blobs and never belong in a bucket.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from workspace.common.logging import scrub
from workspace.common.storage.facade import BlobStorage

# Every top-level directory a module stores blobs under.
BLOB_ROOTS = (
    "files",  # the file tree (files.models.file_upload_path)
    "trash",  # trashed nodes (files.services._trash)
    "thumbnails",  # file previews
    "chat",  # message attachments
    "mail",  # mail attachments
    "projects",  # task attachments
    "ai",  # bot voice references
    "avatars",  # user and group conversation avatars
    "people",  # contact photos
    "faces",  # face crops
)


class Command(BaseCommand):
    help = (
        "Copy every blob from one storage backend to another (see STORAGE_BACKEND "
        "and the S3_* variables). Safe to run again: a blob already at the "
        "destination with the same size is skipped."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--to",
            required=True,
            help="Destination backend: 'local' (MEDIA_ROOT) or 's3'.",
        )
        parser.add_argument(
            "--from",
            dest="source",
            help="Source backend; the other one by default.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be copied without writing anything.",
        )

    def handle(self, *args, **options):
        backends = settings.BLOB_BACKENDS
        target = options["to"]
        source = options["source"] or next(
            (name for name in backends if name != target), None
        )
        for name in (source, target):
            if name not in backends:
                raise CommandError(
                    f"No storage backend {name!r} is configured "
                    f"(configured: {', '.join(sorted(backends))}). "
                    "Object storage needs S3_BUCKET and its S3_* settings."
                )
        if source == target:
            raise CommandError("The source and the destination are the same backend.")

        origin = BlobStorage(**backends[source])
        destination = BlobStorage(**backends[target], allow_overwrite=True)
        dry_run = options["dry_run"]

        copied = skipped = failed = size = 0
        for root in BLOB_ROOTS:
            if not origin.is_dir(root):
                continue
            existing = {blob.name: blob.size for blob in destination.iter_blobs(root)}
            for directory in origin.iter_dirs(root):
                if not dry_run:
                    destination.make_dir(directory)
            for blob in origin.iter_blobs(root):
                if existing.get(blob.name) == blob.size:
                    skipped += 1
                    continue
                if not dry_run:
                    try:
                        with origin.open(blob.name, "rb") as handle:
                            destination.replace(blob.name, handle)
                    except OSError as exc:
                        failed += 1
                        self.stderr.write(f"  {scrub(blob.name)}: {scrub(exc)}")
                        continue
                copied += 1
                size += blob.size
                if copied % 500 == 0:
                    self.stdout.write(f"  {copied} blob(s) copied so far...")

        verb = "Would copy" if dry_run else "Copied"
        self.stdout.write(
            f"{verb} {copied} blob(s), {size} bytes, from {source} to {target}; "
            f"{skipped} already there."
        )
        if failed:
            raise CommandError(f"{failed} blob(s) could not be copied; run again.")
