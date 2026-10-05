"""Bidirectional file sync between disk storage and database."""

import logging
from dataclasses import dataclass, field

from django.core.files.storage import default_storage
from django.utils import timezone

from workspace.common.logging import scrub
from workspace.files.models import File, canonical_name
from workspace.files.services import FileService, relocations

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    files_created: int = 0
    folders_created: int = 0
    files_soft_deleted: int = 0
    folders_soft_deleted: int = 0
    errors: list[str] = field(default_factory=list)


# Columns the walk actually touches. ``path`` is required by
# ``File.soft_delete`` (it builds the descendant filter from it); the rest
# drive the name/type matching against disk entries. ``parent`` selects the
# FK column only - the walk reads ``record.parent_id`` and never traverses
# to the related object, which would be a query per row.
_WALK_FIELDS = ("uuid", "name", "node_type", "parent", "path", "deleted_at")


class _NodeIndex:
    """Name/type lookup of a user's file rows, grouped by parent.

    The walk compares each directory level against the DB by name and node
    type. Querying per level costs a round trip per folder, which multiplies
    by (active users x folders) under the beat schedule; loading the rows
    once and grouping them in memory makes the whole walk a constant number
    of queries regardless of tree size or depth.

    Trashed rows are held separately and only as keys: they exist to stop
    phase 1 from creating a live duplicate next to a node the user deleted,
    never as candidates to recurse into.

    Names are keyed composed (NFC): a row named before names were composed
    still matches the entry it was created for, and an entry written
    decomposed matches the row named after it.
    """

    def __init__(self):
        self._live = {}  # parent_id -> {(composed name, node_type): File}
        self._trashed = {}  # parent_id -> {(composed name, node_type)}

    @classmethod
    def for_subtree(cls, user):
        """Index every personal row the user owns, live and trashed."""
        index = cls()
        live = FileService.user_files_qs(user).only(*_WALK_FIELDS)
        trashed = File.objects.filter(owner=user, deleted_at__isnull=False).values_list(
            "name", "node_type", "parent_id"
        )
        index._absorb(live, trashed)
        return index

    @classmethod
    def for_level(cls, user, parent_db):
        """Index a single directory level - the shallow, on-demand path."""
        index = cls()
        live = (
            FileService.user_files_qs(user).filter(parent=parent_db).only(*_WALK_FIELDS)
        )
        trashed = File.objects.filter(
            owner=user, parent=parent_db, deleted_at__isnull=False
        ).values_list("name", "node_type", "parent_id")
        index._absorb(live, trashed)
        return index

    def _absorb(self, live_qs, trashed_values):
        for record in live_qs:
            bucket = self._live.setdefault(record.parent_id, {})
            bucket[(canonical_name(record.name), record.node_type)] = record
        for name, node_type, parent_id in trashed_values:
            self._trashed.setdefault(parent_id, set()).add(
                (canonical_name(name), node_type)
            )

    @staticmethod
    def _key(parent_db):
        return parent_db.pk if parent_db is not None else None

    def live_at(self, parent_db):
        """Return ``{(name, node_type): File}`` for one level."""
        return self._live.get(self._key(parent_db), {})

    def is_trashed(self, parent_db, name, node_type):
        key = (canonical_name(name), node_type)
        return key in self._trashed.get(self._key(parent_db), set())

    def add(self, file_obj):
        """Register a row the walk just created.

        Phase 1 creates folders that the recursion must then descend into,
        so a freshly created node has to be visible to the same walk.
        """
        bucket = self._live.setdefault(file_obj.parent_id, {})
        bucket[(canonical_name(file_obj.name), file_obj.node_type)] = file_obj


class FileSyncService:
    """Synchronize files between disk storage and database.

    Bidirectional:
    - Disk -> DB: create DB entries for files present on disk but missing in DB.
    - DB -> Disk: soft-delete DB entries whose files no longer exist on disk.
    """

    def __init__(self, *, dry_run=False, log=None):
        self.dry_run = dry_run
        self.log = log or logger
        # Paths a move has not settled yet; read once per walk.
        self._unsettled = set()

    def sync_user_recursive(self, user) -> SyncResult:
        """Full recursive sync for a single user."""
        result = SyncResult()
        storage_prefix = f"files/users/{user.username}"

        if not default_storage.is_dir(storage_prefix):
            return result

        self._unsettled = relocations.journaled_paths()
        self._sync_directory_recursive(
            user=user,
            parent_db=None,
            storage_prefix=storage_prefix,
            result=result,
            index=_NodeIndex.for_subtree(user),
        )
        return result

    def sync_folder_shallow(self, user, parent_db=None) -> SyncResult:
        """Sync immediate children of a specific folder (or root if None)."""
        result = SyncResult()

        storage_prefix = f"files/users/{user.username}"
        if parent_db is not None:
            storage_prefix = f"{storage_prefix}/{parent_db.path or parent_db.name}"

        if not default_storage.is_dir(storage_prefix):
            return result

        self._unsettled = relocations.journaled_paths()
        self._sync_one_level(
            user,
            parent_db,
            storage_prefix,
            result,
            _NodeIndex.for_level(user, parent_db),
        )
        return result

    def _moving(self, name):
        """Whether *name* lies on either side of a move not settled yet."""
        return relocations.covers(self._unsettled, name)

    def _scan(self, storage_prefix, result):
        """Read a directory, recording (not raising) an unreadable path."""
        try:
            return default_storage.scan(storage_prefix)
        except OSError as e:
            result.errors.append(f"Cannot read {storage_prefix}: {e}")
            return None

    def _sync_directory_recursive(self, user, parent_db, storage_prefix, result, index):
        """Sync one directory level, then recurse into subdirectories."""
        subdirectories = self._sync_one_level(
            user, parent_db, storage_prefix, result, index
        )
        for folder_db, stored_name in subdirectories:
            self._sync_directory_recursive(
                user=user,
                parent_db=folder_db,
                storage_prefix=f"{storage_prefix}/{stored_name}",
                result=result,
                index=index,
            )

    def _read_level(self, storage_prefix, result):
        """``{composed name: Entry}`` for one directory, or None if unreadable.

        Two entries whose names differ only in their Unicode form would be
        one node: the composed one is kept, the other is reported and left
        alone, never adopted over it.
        """
        entries = self._scan(storage_prefix, result)
        if entries is None:
            return None
        by_name = {}
        for entry in sorted(entries, key=lambda e: e.name != canonical_name(e.name)):
            name = canonical_name(entry.name)
            if name in by_name:
                result.errors.append(
                    f"Skipped {storage_prefix}/{entry.name}: "
                    f"{by_name[name].name} has the same name in another Unicode form"
                )
                continue
            by_name[name] = entry
        return by_name

    def _sync_one_level(self, user, parent_db, storage_prefix, result, index):
        """Bidirectional sync of immediate children at one directory level.

        Returns ``(folder row, stored name)`` for each directory the level
        holds a live row for, which is where a recursive walk goes next.
        """
        now = timezone.now()

        # --- Read disk entries ---
        disk_names = self._read_level(storage_prefix, result)
        if disk_names is None:
            return []

        db_by_name = index.live_at(parent_db)

        # --- Phase 1: DB -> Disk (soft-delete orphans) ---
        # Before creating anything: a path that flipped from file to
        # directory has to free its name here, or the row created for the
        # new node collides with the stale one.
        for (name, node_type), db_record in list(db_by_name.items()):
            if name in disk_names:
                disk_entry = disk_names[name]
                expected_type = (
                    File.NodeType.FOLDER if disk_entry.is_dir else File.NodeType.FILE
                )
                if expected_type == node_type:
                    continue  # matches, nothing to do

            if self._moving(f"{storage_prefix}/{db_record.name}"):
                continue  # its bytes may be on the other side of the move

            # Not found on disk or type mismatch -> soft-delete
            if self.dry_run:
                self.log.info(
                    "[DRY-RUN] Would soft-delete %s: %s", node_type, scrub(name)
                )
                if node_type == File.NodeType.FOLDER:
                    result.folders_soft_deleted += 1
                else:
                    result.files_soft_deleted += 1
                continue

            try:
                # Bypass FileService.soft_delete here: we already have a custom
                # *deleted_at* (the moment sync started, ``now``), and we still
                # want a single FileEvent for traceability. Calling the model
                # directly + recording the event ourselves preserves both.
                from workspace.files.models import FileEvent
                from workspace.files.services.events import record_event

                count = db_record.soft_delete(deleted_at=now)
                record_event(
                    db_record,
                    user,
                    FileEvent.Action.DELETED,
                    {
                        "cascade_count": count,
                        "detected_by_sync": True,
                    },
                )
                self.log.info(
                    "Soft-deleted %s: %s (%d records)", node_type, scrub(name), count
                )
                if node_type == File.NodeType.FOLDER:
                    result.folders_soft_deleted += 1
                else:
                    result.files_soft_deleted += 1
            except Exception as e:
                result.errors.append(f"Error soft-deleting {name}: {e}")
                self.log.warning("Error soft-deleting %s: %s", scrub(name), scrub(e))

        # --- Phase 2: Disk -> DB (create missing) ---
        subdirectories = []
        for entry_name, entry in disk_names.items():
            is_dir = entry.is_dir
            is_file = entry.is_file

            if not is_dir and not is_file:
                continue  # skip symlinks, special files

            node_type = File.NodeType.FOLDER if is_dir else File.NodeType.FILE

            tracked = db_by_name.get((entry_name, node_type))
            if tracked is not None:
                if is_dir:
                    subdirectories.append((tracked, entry.name))
                continue

            if index.is_trashed(parent_db, entry_name, node_type):
                continue  # in trash, don't create a duplicate

            if self._moving(f"{storage_prefix}/{entry.name}"):
                continue  # a copy or a source of a move: no file of its own

            if self.dry_run:
                self.log.info(
                    "[DRY-RUN] Would create %s: %s", node_type, scrub(entry_name)
                )
                if is_dir:
                    result.folders_created += 1
                else:
                    result.files_created += 1
                continue

            try:
                if entry.name != entry_name:
                    # Written decomposed (a Mac), named composed: the blob
                    # moves to the name the row will carry first.
                    default_storage.move(
                        f"{storage_prefix}/{entry.name}",
                        f"{storage_prefix}/{entry_name}",
                    )
                if is_dir:
                    created = FileService.create_folder(
                        user, entry_name, parent_db, acting_user=user
                    )
                    index.add(created)
                    subdirectories.append((created, entry_name))
                    result.folders_created += 1
                    self.log.info("Created folder: %s", scrub(entry_name))
                else:
                    content_path = f"{storage_prefix}/{entry_name}"

                    try:
                        size = default_storage.size(content_path)
                    except OSError:
                        size = None

                    created = FileService.register_disk_file(
                        user,
                        entry_name,
                        parent_db,
                        content_path,
                        size=size,
                        acting_user=user,
                    )
                    index.add(created)
                    result.files_created += 1
                    self.log.info(
                        "Created file: %s (%s bytes)", scrub(entry_name), size
                    )

            except Exception as e:
                result.errors.append(f"Error creating {entry_name}: {e}")
                self.log.warning("Error creating %s: %s", scrub(entry_name), scrub(e))
        return subdirectories
