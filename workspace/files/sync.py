"""Bidirectional file sync between disk storage and database."""

import errno
import logging
import os
from dataclasses import dataclass, field

from django.core.files.storage import default_storage
from django.utils import timezone

from workspace.common.logging import scrub
from workspace.common.text import caseless
from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.services._storage_ops import unique_copy_name
from workspace.files.services.shortcuts import rename_aside

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    files_created: int = 0
    folders_created: int = 0
    files_soft_deleted: int = 0
    folders_soft_deleted: int = 0
    shortcuts_renamed: int = 0
    disk_entries_renamed: int = 0
    renames_followed: int = 0
    errors: list[str] = field(default_factory=list)


# Columns the walk actually touches. ``path`` is required by
# ``File.soft_delete`` (it builds the descendant filter from it); the rest
# drive the name/type matching against disk entries. ``parent`` selects the
# FK column only - the walk reads ``record.parent_id`` and never traverses
# to the related object, which would be a query per row.
_WALK_FIELDS = ("uuid", "name", "node_type", "parent", "path", "deleted_at")


def _live_nodes(queryset):
    return queryset.only(*_WALK_FIELDS)


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

    Live rows are also held by caseless name, the key of the live name
    constraint, which is what a disk entry has to be registered under.
    Shortcuts are held there alone: they exist in the database only, so
    matching them against disk would trash every one of them as gone from
    the disk.
    """

    def __init__(self):
        self._live = {}  # parent_id -> {(name, node_type): File}, no shortcut
        self._holders = {}  # parent_id -> {caseless(name): File}
        self._trashed = {}  # parent_id -> {(name, node_type)}

    @classmethod
    def for_subtree(cls, user):
        """Index every personal row the user owns, live and trashed."""
        index = cls()
        live = _live_nodes(FileService.user_files_qs(user))
        trashed = File.objects.filter(owner=user, deleted_at__isnull=False).values_list(
            "name", "node_type", "parent_id"
        )
        index._absorb(live, trashed)
        return index

    @classmethod
    def for_level(cls, user, parent_db):
        """Index a single directory level - the shallow, on-demand path."""
        index = cls()
        live = _live_nodes(FileService.user_files_qs(user).filter(parent=parent_db))
        trashed = File.objects.filter(
            owner=user, parent=parent_db, deleted_at__isnull=False
        ).values_list("name", "node_type", "parent_id")
        index._absorb(live, trashed)
        return index

    def _absorb(self, live_qs, trashed_values):
        for record in live_qs:
            self._hold(record)
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

    def holder_of(self, parent_db, name):
        """The live node of that level whose name *name* clashes with."""
        return self._holders.get(self._key(parent_db), {}).get(caseless(name))

    def names_at(self, parent_db):
        """The caseless names the live nodes of one level hold."""
        return self._holders.get(self._key(parent_db), {}).keys()

    def add(self, file_obj):
        """Register a row the walk just created.

        Phase 2 creates folders that the recursion must then descend into,
        so a freshly created node has to be visible to the same walk.
        """
        self._hold(file_obj)

    def discard(self, file_obj):
        """Forget a row the walk just trashed: its name is free again."""
        self._live.get(file_obj.parent_id, {}).pop(
            (file_obj.name, file_obj.node_type), None
        )
        self._holders.get(file_obj.parent_id, {}).pop(caseless(file_obj.name), None)

    def renamed(self, file_obj, old_name, old_path=None):
        """Re-key a row the walk just renamed.

        A folder's rename rewrote the paths below it in the database, and the
        rows held here were loaded before it: trashing one of them builds its
        subtree from its ``path``, which has to be the new one.
        """
        self._live.get(file_obj.parent_id, {}).pop((old_name, file_obj.node_type), None)
        self._holders[file_obj.parent_id].pop(caseless(old_name))
        self._hold(file_obj)
        if old_path and file_obj.node_type == File.NodeType.FOLDER:
            prefix = f"{old_path}/"
            for bucket in self._holders.values():
                for record in bucket.values():
                    if record.path and record.path.startswith(prefix):
                        record.path = f"{file_obj.path}/{record.path[len(prefix) :]}"

    def _hold(self, record):
        self._holders.setdefault(record.parent_id, {})[caseless(record.name)] = record
        if record.node_type != File.NodeType.SHORTCUT:
            bucket = self._live.setdefault(record.parent_id, {})
            bucket[(record.name, record.node_type)] = record


def _node_type_of(entry):
    if entry.is_dir(follow_symlinks=False):
        return File.NodeType.FOLDER
    if entry.is_file(follow_symlinks=False):
        return File.NodeType.FILE
    return None


class FileSyncService:
    """Synchronize files between disk storage and database.

    Bidirectional:
    - Disk -> DB: create DB entries for files present on disk but missing in DB.
    - DB -> Disk: soft-delete DB entries whose files no longer exist on disk.

    A row's name is its name on disk: that is how the next run finds it.
    Names are matched the way the live name constraint compares them, without
    case or accent encoding, whether or not the disk does:

    - A row whose name is gone from the disk follows an entry of the same
      type spelled differently. On a disk that folds names (Windows, macOS)
      it is the same file, and trashing the row would carry its bytes into
      the trash; on any disk the node keeps its shares, tags and history.
    - A disk entry whose name a live node already holds - a shortcut, or a
      sibling spelled differently, which a case-sensitive disk keeps side by
      side - is reconciled before it is registered: the shortcut, which has
      no name on disk, gives the name up; otherwise the newcomer is renamed
      to ``name (Copy N)`` on disk.
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

        renamed = self._sync_one_level(
            user, disk_path, parent_db, storage_prefix, result, index, entries=entries
        )

        live_here = index.live_at(parent_db)
        for entry in entries:
            if not entry.is_dir(follow_symlinks=False):
                continue

            name = renamed.get(entry.name, entry.name)
            if index.is_trashed(parent_db, name, File.NodeType.FOLDER):
                continue

            folder_db = live_here.get((name, File.NodeType.FOLDER))
            if folder_db:
                self._sync_directory_recursive(
                    user=user,
                    disk_path=os.path.join(disk_path, name),
                    parent_db=folder_db,
                    storage_prefix=f"{storage_prefix}/{name}",
                    result=result,
                    index=index,
                )

    def _sync_one_level(
        self, user, disk_path, parent_db, storage_prefix, result, index, entries=None
    ):
        """Bidirectional sync of immediate children at one directory level.

        Returns ``{name scanned: name given}`` for the disk entries it renamed.
        """
        now = timezone.now()

        # --- Read disk entries ---
        if entries is None:
            entries = self._scan(disk_path, result)
            if entries is None:
                return {}

        disk_names = {}  # name -> DirEntry
        for entry in entries:
            disk_names[entry.name] = entry

        db_by_name = index.live_at(parent_db)

        # --- Phase 1: DB -> Disk (soft-delete orphans) ---
        # Before creating anything: a path that flipped from file to
        # directory has to free its name here, or the row created for the
        # new node collides with the stale one.
        spellings = None  # caseless(name) -> disk names, built on first need
        followed = set()  # disk names a row of this level took over
        for (name, node_type), db_record in list(db_by_name.items()):
            if name in disk_names:
                disk_entry = disk_names[name]
                is_dir = disk_entry.is_dir(follow_symlinks=False)
                expected_type = File.NodeType.FOLDER if is_dir else File.NodeType.FILE
                if expected_type == node_type:
                    continue  # matches, nothing to do
            else:
                if spellings is None:
                    spellings = {}
                    for disk_name in sorted(disk_names):
                        spellings.setdefault(caseless(disk_name), []).append(disk_name)
                new_name = next(
                    (
                        disk_name
                        for disk_name in spellings.get(caseless(name), ())
                        if _node_type_of(disk_names[disk_name]) == node_type
                    ),
                    None,
                )
                if new_name is not None:
                    followed.add(new_name)
                    self._follow_rename(user, db_record, new_name, index, result)
                    continue

            # Not found on disk or type mismatch -> soft-delete
            if self.dry_run:
                self.log.info(
                    "[DRY-RUN] Would soft-delete %s: %s", node_type, scrub(name)
                )
                if node_type == File.NodeType.FOLDER:
                    result.folders_soft_deleted += 1
                else:
                    result.files_soft_deleted += 1
                index.discard(db_record)
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
                index.discard(db_record)
            except Exception as e:
                result.errors.append(f"Error soft-deleting {name}: {e}")
                self.log.warning("Error soft-deleting %s: %s", scrub(name), scrub(e))

        # --- Phase 2: Disk -> DB (create missing) ---
        # By name, so that of two newcomers clashing caselessly the same one
        # keeps its name on every run.
        renamed = {}
        for entry_name, entry in sorted(disk_names.items()):
            is_dir = entry.is_dir(follow_symlinks=False)
            is_file = entry.is_file(follow_symlinks=False)

            if not is_dir and not is_file:
                continue  # skip symlinks, special files

            node_type = File.NodeType.FOLDER if is_dir else File.NodeType.FILE

            if entry_name in followed or (entry_name, node_type) in db_by_name:
                continue  # already tracked

            if index.is_trashed(parent_db, entry_name, node_type):
                continue  # in trash, don't create a duplicate

            name = entry_name
            shortcut = None
            holder = index.holder_of(parent_db, entry_name)
            if holder is not None and holder.node_type == File.NodeType.SHORTCUT:
                shortcut = holder
            elif holder is not None:
                taken = {*index.names_at(parent_db), *disk_names, *renamed.values()}
                name = unique_copy_name(entry_name, node_type, taken)

            if self.dry_run:
                if shortcut is not None:
                    self.log.info(
                        "[DRY-RUN] Would rename shortcut: %s", scrub(shortcut.name)
                    )
                    result.shortcuts_renamed += 1
                if name != entry_name:
                    self.log.info(
                        "[DRY-RUN] Would rename on disk: %s -> %s",
                        scrub(entry_name),
                        scrub(name),
                    )
                    result.disk_entries_renamed += 1
                self.log.info("[DRY-RUN] Would create %s: %s", node_type, scrub(name))
                if is_dir:
                    result.folders_created += 1
                else:
                    result.files_created += 1
                continue

            try:
                if shortcut is not None:
                    keep_free = {*disk_names, *renamed.values()}
                    self._rename_shortcut_aside(
                        user, shortcut, keep_free, index, result
                    )
                if name != entry_name:
                    self._rename_on_disk(disk_path, entry_name, name)
                    renamed[entry_name] = name
                    result.disk_entries_renamed += 1
                    self.log.info(
                        "Renamed on disk: %s -> %s, a sibling holds the name",
                        scrub(entry_name),
                        scrub(name),
                    )
                if is_dir:
                    created = FileService.create_folder(
                        user, name, parent_db, acting_user=user
                    )
                    index.add(created)
                    result.folders_created += 1
                    self.log.info("Created folder: %s", scrub(name))
                else:
                    try:
                        size = os.lstat(os.path.join(disk_path, name)).st_size
                    except OSError:
                        size = None

                    created = FileService.register_disk_file(
                        user,
                        name,
                        parent_db,
                        f"{storage_prefix}/{name}",
                        size=size,
                        acting_user=user,
                    )
                    index.add(created)
                    result.files_created += 1
                    self.log.info("Created file: %s (%s bytes)", scrub(name), size)

            except Exception as e:
                result.errors.append(f"Error creating {entry_name}: {e}")
                self.log.warning("Error creating %s: %s", scrub(entry_name), scrub(e))

        return renamed

    def _follow_rename(self, user, db_record, new_name, index, result):
        old_name, old_path = db_record.name, db_record.path
        if self.dry_run:
            self.log.info(
                "[DRY-RUN] Would follow rename: %s -> %s",
                scrub(old_name),
                scrub(new_name),
            )
            result.renames_followed += 1
            return
        try:
            FileService.rename(db_record, new_name, acting_user=user)
        except Exception as e:
            # The rename rolled back, not the attributes it set on the row.
            db_record.refresh_from_db()
            result.errors.append(f"Error renaming {old_name}: {e}")
            self.log.warning("Error renaming %s: %s", scrub(old_name), scrub(e))
            return
        index.renamed(db_record, old_name, old_path)
        result.renames_followed += 1
        self.log.info("Followed rename: %s -> %s", scrub(old_name), scrub(new_name))

    def _rename_shortcut_aside(self, user, shortcut, keep_free, index, result):
        old_name = shortcut.name
        rename_aside(shortcut, keep_free=keep_free, acting_user=user)
        index.renamed(shortcut, old_name)
        result.shortcuts_renamed += 1
        self.log.info(
            "Renamed shortcut %s to %s: a disk entry holds its name",
            scrub(old_name),
            scrub(shortcut.name),
        )

    @staticmethod
    def _rename_on_disk(disk_path, old_name, new_name):
        source = os.path.join(disk_path, old_name)
        destination = os.path.join(disk_path, new_name)
        # os.rename replaces an existing file on POSIX without a word, and the
        # walk never writes over what someone put on the disk.
        if os.path.lexists(destination):
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), new_name)
        os.rename(source, destination)
