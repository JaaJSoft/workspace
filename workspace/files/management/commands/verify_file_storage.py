"""Check that the stored blobs mirror the file tree.

A file's blob sits at its node's tree path (under ``trash/`` while trashed),
and a folder has a directory, kept by a marker on object storage when empty.
That is what makes a bucket or a media directory readable without the
database, and what the sync relies on; nothing else checks it.
"""

import posixpath

from django.core.management.base import BaseCommand, CommandError

from workspace.common.logging import scrub
from workspace.files.models import File
from workspace.files.services import _storage_ops, _trash


def _expected_blob(node):
    if node.deleted_at is not None:
        return _trash.trashed_storage_path(node)
    return posixpath.join(_storage_ops.live_parent_path(node), node.name)


class Command(BaseCommand):
    help = (
        "Verify that every file's blob exists at its tree path and every folder "
        "has a directory; list the blobs no file points at."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix-dirs",
            action="store_true",
            help=(
                "Create the directory of every folder that has none - an empty "
                "folder copied by a tool that skips empty directories."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=20,
            help="How many of each problem to list (default 20).",
        )

    def handle(self, *args, **options):
        storage = File._meta.get_field("content").storage
        blobs, directories = set(), set()
        for root in ("files", _trash.TRASH_ROOT):
            if storage.is_dir(root):
                blobs.update(blob.name for blob in storage.iter_blobs(root))
                directories.update(storage.iter_dirs(root))

        missing, misplaced, dirless, referenced = [], [], [], set()
        nodes = File.objects.select_related("owner", "parent__owner").order_by("path")
        for node in nodes.iterator(chunk_size=2000):
            if node.node_type == File.NodeType.FOLDER:
                if _storage_ops.folder_storage_path(node) not in directories:
                    dirless.append(node)
                continue
            if not node.content:
                continue
            name = node.content.name
            referenced.add(name)
            if name not in blobs:
                missing.append(f"{node.pk} {name}")
            elif name != _expected_blob(node):
                misplaced.append(f"{node.pk} {name} (expected {_expected_blob(node)})")
        orphans = sorted(blobs - referenced)

        self._report("file(s) whose blob is missing", missing, options["limit"])
        self._report("blob(s) off their file's tree path", misplaced, options["limit"])
        self._report("blob(s) no file points at", orphans, options["limit"])
        self._report(
            "folder(s) without a directory",
            [f"{node.pk} {_storage_ops.folder_storage_path(node)}" for node in dirless],
            options["limit"],
        )

        if dirless and options["fix_dirs"]:
            for node in dirless:
                storage.make_dir(_storage_ops.folder_storage_path(node))
            self.stdout.write(f"Created {len(dirless)} folder directory(ies).")
        if missing:
            raise CommandError(f"{len(missing)} file(s) point at a blob that is gone.")
        self.stdout.write(self.style.SUCCESS("Every file's blob is in storage."))

    def _report(self, label, items, limit):
        self.stdout.write(f"{len(items)} {label}")
        for item in items[:limit]:
            self.stdout.write(f"  {scrub(item)}")
        if len(items) > limit:
            self.stdout.write(f"  ... and {len(items) - limit} more")
