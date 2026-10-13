"""Check that the stored blobs mirror the file tree, and put right what can be.

A file's blob sits at its node's tree path (under ``trash/`` while trashed),
and a folder has a directory, kept by a marker on object storage when empty.
That is what makes a bucket or a media directory readable without the
database, and what the sync relies on; nothing else checks it.
"""

import posixpath
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from workspace.common.logging import scrub
from workspace.files.models import File, canonical_name
from workspace.files.services import FileService, _storage_ops, _trash, relocations


def _expected_blob(node):
    if node.deleted_at is not None:
        return _trash.trashed_storage_path(node)
    return posixpath.join(_storage_ops.live_parent_path(node), node.name)


class Command(BaseCommand):
    help = (
        "Verify that every file's blob exists at its tree path and every folder "
        "has a directory; list the blobs no file points at. --repair puts right "
        "what can be."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--repair",
            action="store_true",
            help=(
                "Settle the moves a dead worker or a rolled-back transaction "
                "left unfinished, compose the names stored decomposed, move each "
                "blob off its tree path back onto it, and give every folder "
                "without a directory one."
            ),
        )
        parser.add_argument(
            "--settle-after",
            type=int,
            default=int(relocations.SETTLE_AFTER.total_seconds() // 60),
            metavar="MINUTES",
            help=(
                "Only settle the moves journaled at least this long ago (default "
                "a day). Settling a move still running would undo it: lower it "
                "only with the app stopped."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=20,
            help="How many of each problem to list (default 20).",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        if options["repair"]:
            self._repair(timedelta(minutes=options["settle_after"]), limit)

        storage = File._meta.get_field("content").storage
        blobs, directories = set(), set()
        for root in ("files", _trash.TRASH_ROOT):
            if storage.is_dir(root):
                blobs.update(blob.name for blob in storage.iter_blobs(root))
                directories.update(storage.iter_dirs(root))

        missing, misplaced, dirless, decomposed, referenced = [], [], [], [], set()
        nodes = File.objects.select_related("owner", "parent__owner").order_by("path")
        for node in nodes.iterator(chunk_size=2000):
            if node.deleted_at is None and node.name != canonical_name(node.name):
                decomposed.append(f"{node.pk} {node.path}")
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
        pending = relocations.entries()

        self._report("file(s) whose blob is missing", missing, limit)
        self._report("blob(s) off their file's tree path", misplaced, limit)
        self._report("blob(s) no file points at", orphans, limit)
        self._report(
            "folder(s) without a directory",
            [f"{node.pk} {_storage_ops.folder_storage_path(node)}" for node in dirless],
            limit,
        )
        self._report("name(s) stored decomposed", decomposed, limit)
        self._report(
            "move(s) not finished yet",
            [
                f"{entry.source} -> {entry.destination} ({entry.written:%Y-%m-%d %H:%M})"
                for entry in pending
            ],
            limit,
        )

        if dirless and options["repair"]:
            for node in dirless:
                storage.make_dir(_storage_ops.folder_storage_path(node))
            self.stdout.write(f"Created {len(dirless)} folder directory(ies).")
        if missing:
            raise CommandError(f"{len(missing)} file(s) point at a blob that is gone.")
        self.stdout.write(self.style.SUCCESS("Every file's blob is in storage."))

    def _repair(self, settle_after, limit):
        settled = relocations.settle_stale(older_than=settle_after)
        self.stdout.write(
            f"Settled {settled.entries} unfinished move(s): dropped "
            f"{len(settled.dropped)} leftover(s), put {len(settled.restored)} "
            f"blob(s) back where their file points."
        )
        self._report(
            "blob(s) of those moves no file points at, kept", settled.undecided, limit
        )

        renamed, refused = 0, []
        live = File.objects.filter(deleted_at__isnull=True)
        decomposed = [
            (pk, path)
            for pk, name, path in live.values_list("pk", "name", "path").iterator()
            if name != canonical_name(name)
        ]
        # Deepest first, each row read fresh: renaming a folder rewrites the
        # rows inside it, which an instance loaded before would write back.
        for pk, path in sorted(decomposed, key=lambda row: -row[1].count("/")):
            node = File.objects.select_related("owner", "parent__owner").get(pk=pk)
            try:
                FileService.rename(node, canonical_name(node.name))
                renamed += 1
            except ValueError as exc:
                refused.append(f"{pk} {path}: {exc}")
        self.stdout.write(f"Composed {renamed} decomposed name(s).")
        self._report("name(s) left decomposed", refused, limit)

        moved, repointed, kept = 0, 0, []
        storage = File._meta.get_field("content").storage
        files = File.objects.filter(node_type=File.NodeType.FILE).select_related(
            "owner", "parent__owner"
        )
        for node in (
            files.exclude(content="").order_by("path").iterator(chunk_size=2000)
        ):
            name, expected = node.content.name, _expected_blob(node)
            if name == expected:
                continue
            if not storage.exists(expected):
                if storage.exists(name):
                    with transaction.atomic():
                        _storage_ops.move_node_storage(node, name, expected)
                    moved += 1
            elif (
                not storage.exists(name)
                and not File.objects.filter(content=expected).exists()
            ):
                # The blob already sits at the tree path and nobody else's:
                # only the row was left behind.
                File.objects.filter(pk=node.pk).update(content=expected)
                repointed += 1
            else:
                kept.append(f"{node.pk} {name}: {expected} is taken")
        self.stdout.write(
            f"Moved {moved} blob(s) onto their tree path, repointed {repointed} "
            "file(s) at the blob already there."
        )
        self._report("blob(s) left off their tree path", kept, limit)

    def _report(self, label, items, limit):
        self.stdout.write(f"{len(items)} {label}")
        for item in items[:limit]:
            self.stdout.write(f"  {scrub(item)}")
        if len(items) > limit:
            self.stdout.write(f"  ... and {len(items) - limit} more")
