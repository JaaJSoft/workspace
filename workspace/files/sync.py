"""Bidirectional file sync between disk storage and database."""

import logging
import os
from dataclasses import dataclass, field

from django.core.files.storage import default_storage
from django.utils import timezone

from workspace.files.models import File
from workspace.files.services import FileService

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
# drive the name/type matching against disk entries.
_WALK_FIELDS = ("uuid", "name", "node_type", "parent_id", "path", "deleted_at")


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
    """

    def __init__(self):
        self._live = {}  # parent_id -> {(name, node_type): File}
        self._trashed = {}  # parent_id -> {(name, node_type)}

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
            bucket[(record.name, record.node_type)] = record
        for name, node_type, parent_id in trashed_values:
            self._trashed.setdefault(parent_id, set()).add((name, node_type))

    @staticmethod
    def _key(parent_db):
        return parent_db.pk if parent_db is not None else None

    def live_at(self, parent_db):
        """Return ``{(name, node_type): File}`` for one level."""
        return self._live.get(self._key(parent_db), {})

    def is_trashed(self, parent_db, name, node_type):
        return (name, node_type) in self._trashed.get(self._key(parent_db), set())

    def add(self, file_obj):
        """Register a row the walk just created.

        Phase 1 creates folders that the recursion must then descend into,
        so a freshly created node has to be visible to the same walk.
        """
        bucket = self._live.setdefault(file_obj.parent_id, {})
        bucket[(file_obj.name, file_obj.node_type)] = file_obj


class FileSyncService:
    """Synchronize files between disk storage and database.

    Bidirectional:
    - Disk -> DB: create DB entries for files present on disk but missing in DB.
    - DB -> Disk: soft-delete DB entries whose files no longer exist on disk.
    """

    def __init__(self, *, dry_run=False, log=None):
        self.dry_run = dry_run
        self.log = log or logger

    def sync_user_recursive(self, user) -> SyncResult:
        """Full recursive sync for a single user."""
        result = SyncResult()
        user_dir = os.path.join(
            default_storage.location, "files", "users", user.username
        )

        if not os.path.isdir(user_dir):
            return result

        self._sync_directory_recursive(
            user=user,
            disk_path=user_dir,
            parent_db=None,
            storage_prefix=f"files/users/{user.username}",
            result=result,
            index=_NodeIndex.for_subtree(user),
        )
        return result

    def sync_folder_shallow(self, user, parent_db=None) -> SyncResult:
        """Sync immediate children of a specific folder (or root if None)."""
        result = SyncResult()

        if parent_db is None:
            disk_path = os.path.join(
                default_storage.location, "files", "users", user.username
            )
            storage_prefix = f"files/users/{user.username}"
        else:
            disk_path = os.path.join(
                default_storage.location,
                "files",
                "users",
                user.username,
                *parent_db.path.split("/") if parent_db.path else [parent_db.name],
            )
            storage_prefix = (
                f"files/users/{user.username}/{parent_db.path or parent_db.name}"
            )

        if not os.path.isdir(disk_path):
            return result

        self._sync_one_level(
            user,
            disk_path,
            parent_db,
            storage_prefix,
            result,
            _NodeIndex.for_level(user, parent_db),
        )
        return result

    def _scan(self, disk_path, result):
        """Read a directory, recording (not raising) an unreadable path."""
        try:
            return list(os.scandir(disk_path))
        except OSError as e:
            result.errors.append(f"Cannot read {disk_path}: {e}")
            return None

    def _sync_directory_recursive(
        self, user, disk_path, parent_db, storage_prefix, result, index
    ):
        """Sync one directory level, then recurse into subdirectories."""
        entries = self._scan(disk_path, result)
        if entries is None:
            return

        self._sync_one_level(
            user, disk_path, parent_db, storage_prefix, result, index, entries=entries
        )

        live_here = index.live_at(parent_db)
        for entry in entries:
            if not entry.is_dir(follow_symlinks=False):
                continue

            if index.is_trashed(parent_db, entry.name, File.NodeType.FOLDER):
                continue

            folder_db = live_here.get((entry.name, File.NodeType.FOLDER))
            if folder_db:
                self._sync_directory_recursive(
                    user=user,
                    disk_path=entry.path,
                    parent_db=folder_db,
                    storage_prefix=f"{storage_prefix}/{entry.name}",
                    result=result,
                    index=index,
                )

    def _sync_one_level(
        self, user, disk_path, parent_db, storage_prefix, result, index, entries=None
    ):
        """Bidirectional sync of immediate children at one directory level."""
        now = timezone.now()

        # --- Read disk entries ---
        if entries is None:
            entries = self._scan(disk_path, result)
            if entries is None:
                return

        disk_names = {}  # name -> DirEntry
        for entry in entries:
            disk_names[entry.name] = entry

        db_by_name = index.live_at(parent_db)

        # --- Phase 1: Disk -> DB (create missing) ---
        for entry_name, entry in disk_names.items():
            is_dir = entry.is_dir(follow_symlinks=False)
            is_file = entry.is_file(follow_symlinks=False)

            if not is_dir and not is_file:
                continue  # skip symlinks, special files

            node_type = File.NodeType.FOLDER if is_dir else File.NodeType.FILE

            if (entry_name, node_type) in db_by_name:
                continue  # already tracked

            if index.is_trashed(parent_db, entry_name, node_type):
                continue  # in trash, don't create a duplicate

            if self.dry_run:
                self.log.info("[DRY-RUN] Would create %s: %s", node_type, entry_name)
                if is_dir:
                    result.folders_created += 1
                else:
                    result.files_created += 1
                continue

            try:
                if is_dir:
                    created = FileService.create_folder(
                        user, entry_name, parent_db, acting_user=user
                    )
                    index.add(created)
                    result.folders_created += 1
                    self.log.info("Created folder: %s", entry_name)
                else:
                    content_path = f"{storage_prefix}/{entry_name}"

                    try:
                        size = entry.stat(follow_symlinks=False).st_size
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
                    self.log.info("Created file: %s (%s bytes)", entry_name, size)

            except Exception as e:
                result.errors.append(f"Error creating {entry_name}: {e}")
                self.log.warning("Error creating %s: %s", entry_name, e)

        # --- Phase 2: DB -> Disk (soft-delete orphans) ---
        # Iterate a snapshot: phase 1 may have registered new rows into this
        # same mapping via the index. They are known-present on disk, so they
        # fall through the match below either way - the copy just keeps the
        # loop independent of whether phase 1 touched the bucket.
        for (name, node_type), db_record in list(db_by_name.items()):
            if name in disk_names:
                disk_entry = disk_names[name]
                is_dir = disk_entry.is_dir(follow_symlinks=False)
                expected_type = File.NodeType.FOLDER if is_dir else File.NodeType.FILE
                if expected_type == node_type:
                    continue  # matches, nothing to do

            # Not found on disk or type mismatch -> soft-delete
            if self.dry_run:
                self.log.info("[DRY-RUN] Would soft-delete %s: %s", node_type, name)
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
                    "Soft-deleted %s: %s (%d records)", node_type, name, count
                )
                if node_type == File.NodeType.FOLDER:
                    result.folders_soft_deleted += 1
                else:
                    result.files_soft_deleted += 1
            except Exception as e:
                result.errors.append(f"Error soft-deleting {name}: {e}")
                self.log.warning("Error soft-deleting %s: %s", name, e)
