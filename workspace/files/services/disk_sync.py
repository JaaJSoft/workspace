"""Reconciling a user's directory on storage with their rows, both ways.

- Disk -> DB: a row is created for what the disk holds and no row answers for.
- DB -> disk: a row whose bytes are gone from the disk goes to the trash.

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
- Names are stored composed (NFC), so an entry written decomposed (a Mac)
  is respelled composed on disk: before it is registered, or when the
  row of that name already exists.

Only a user's personal tree is walked: group folders are not synced.
"""

import logging
from dataclasses import dataclass, field, fields
from datetime import datetime

from django.core.files.storage import default_storage
from django.utils import timezone
from django.utils.translation import gettext

from workspace.common.logging import scrub
from workspace.common.text import caseless

from ..models import File, canonical_name
from ..queries import user_files_qs
from ..storage_layout import user_root
from . import relocations, trash, tree
from .shortcuts import rename_aside
from .storage_ops import unique_copy_name

logger = logging.getLogger(__name__)


class NotPersonalFolder(ValueError):
    """A folder outside the user's personal tree was asked to be synced.

    The walk reads the user's own directory: a group's folder, or one that is
    trashed, has nothing there, and whatever sits at the same path belongs to
    another folder.
    """


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

    def counts(self):
        """Every counter by name, as each report of a sync shows them."""
        return {
            f.name: getattr(self, f.name) for f in fields(self) if f.name != "errors"
        }


# Columns the walk actually touches. ``path`` is required by
# ``trash.trash_subtree`` (it builds the descendant filter from it); the rest
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

    Trashed rows are held separately and only as keys, composed: they exist
    to stop the adoption from creating a live duplicate next to a node the user
    deleted, never as candidates to recurse into.

    Live rows are also held by caseless name, the key of the live name
    constraint, which is what a disk entry has to be registered under.
    Shortcuts are held there alone: they exist in the database only, so
    matching them against disk would trash every one of them as gone from
    the disk.
    """

    def __init__(self):
        self._live = {}  # parent_id -> {(name, node_type): File}, no shortcut
        self._holders = {}  # parent_id -> {caseless(name): File}
        self._trashed = {}  # parent_id -> {(composed name, node_type)}

    @classmethod
    def for_subtree(cls, user):
        """Index every personal row the user owns, live and trashed."""
        index = cls()
        live = _live_nodes(user_files_qs(user))
        trashed = File.objects.filter(owner=user, deleted_at__isnull=False).values_list(
            "name", "node_type", "parent_id"
        )
        index._absorb(live, trashed)
        return index

    @classmethod
    def for_level(cls, user, parent_db):
        """Index a single directory level - the shallow, on-demand path."""
        index = cls()
        live = _live_nodes(user_files_qs(user).filter(parent=parent_db))
        trashed = File.objects.filter(
            owner=user, parent=parent_db, deleted_at__isnull=False
        ).values_list("name", "node_type", "parent_id")
        index._absorb(live, trashed)
        return index

    def _absorb(self, live_qs, trashed_values):
        for record in live_qs:
            self._hold(record)
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

    def holder_of(self, parent_db, name):
        """The live node of that level whose name *name* clashes with."""
        return self._holders.get(self._key(parent_db), {}).get(caseless(name))

    def names_at(self, parent_db):
        """The caseless names the live nodes of one level hold."""
        return self._holders.get(self._key(parent_db), {}).keys()

    def add(self, file_obj):
        """Register a row the walk just created.

        The adoption creates folders that the recursion must then descend into,
        so a freshly created node has to be visible to the same walk.
        """
        self._hold(file_obj)

    def discard(self, file_obj):
        """Forget a row the walk just trashed: its name is free again."""
        self._live.get(file_obj.parent_id, {}).pop(
            (file_obj.name, file_obj.node_type), None
        )
        self._holders.get(file_obj.parent_id, {}).pop(caseless(file_obj.name), None)

    def renamed(self, file_obj, old_name):
        """Re-key a row the walk just renamed.

        A folder's rename rewrote the paths below it in the database, and the
        rows held here were loaded before it: trashing one of them builds its
        subtree from its ``path``, which has to be the new one.
        """
        self._live.get(file_obj.parent_id, {}).pop((old_name, file_obj.node_type), None)
        self._holders[file_obj.parent_id].pop(caseless(old_name))
        self._hold(file_obj)
        if file_obj.node_type == File.NodeType.FOLDER:
            folders = [file_obj]
            while folders:
                folder = folders.pop()
                for child in self._holders.get(folder.pk, {}).values():
                    child.path = f"{folder.path}/{child.name}"
                    if child.node_type == File.NodeType.FOLDER:
                        folders.append(child)

    def _hold(self, record):
        self._holders.setdefault(record.parent_id, {})[caseless(record.name)] = record
        if record.node_type != File.NodeType.SHORTCUT:
            bucket = self._live.setdefault(record.parent_id, {})
            bucket[(record.name, record.node_type)] = record


def _node_type_of(entry):
    if entry.is_dir:
        return File.NodeType.FOLDER
    if entry.is_file:
        return File.NodeType.FILE
    return None


@dataclass
class _Walk:
    """What one run carries from level to level."""

    user: object
    index: _NodeIndex
    dry_run: bool
    log: logging.Logger
    result: SyncResult
    # Paths a move has not settled yet; read once per walk.
    unsettled: set

    def moving(self, name):
        """Whether *name* lies on either side of a move not settled yet."""
        return relocations.covers(self.unsettled, name)

    def scan(self, storage_prefix):
        """Read a directory, recording (not raising) an unreadable path."""
        try:
            return default_storage.scan(storage_prefix)
        except OSError as e:
            self.result.errors.append(f"Cannot read {storage_prefix}: {e}")
            return None


@dataclass
class _Level:
    """One directory being reconciled, and what the walk did to it so far."""

    walk: _Walk
    parent_db: File | None
    storage_prefix: str
    disk_names: dict  # name -> Entry
    rows: dict  # (name, node_type) -> File, the live rows of the level
    # When the level's sync started, so every node it trashes shares one moment.
    now: datetime
    followed: set = field(default_factory=set)  # disk names a row took over
    untrashed: set = field(default_factory=set)  # pks of the rows not trashed
    renamed: dict = field(default_factory=dict)  # disk name scanned -> given
    _spellings: dict | None = None  # caseless(name) -> disk names

    def spelled_otherwise(self, name, node_type):
        """The disk entry of *node_type* that is *name* in another spelling."""
        if self._spellings is None:
            self._spellings = {}
            for disk_name in sorted(self.disk_names):
                self._spellings.setdefault(caseless(disk_name), []).append(disk_name)
        return next(
            (
                disk_name
                for disk_name in self._spellings.get(caseless(name), ())
                if _node_type_of(self.disk_names[disk_name]) == node_type
            ),
            None,
        )


def sync_user_tree(user, *, dry_run=False, log=None) -> SyncResult:
    """Sync *user*'s whole personal tree with their directory."""
    result = SyncResult()
    storage_prefix = user_root(user.username)

    if not default_storage.is_dir(storage_prefix):
        return result

    unsettled = relocations.journaled_paths()
    walk = _Walk(
        user, _NodeIndex.for_subtree(user), dry_run, log or logger, result, unsettled
    )
    _sync_subtree(walk, None, storage_prefix)
    return result


def sync_folder_level(user, folder=None, *, dry_run=False, log=None) -> SyncResult:
    """Sync the immediate children of *folder*, or of *user*'s root if None.

    Raises ``NotPersonalFolder`` for a folder that is not a live one of
    *user*'s personal tree.
    """
    if folder is not None and not (
        folder.node_type == File.NodeType.FOLDER
        and folder.owner_id == user.pk
        and folder.group_id is None
        and folder.deleted_at is None
    ):
        raise NotPersonalFolder(
            gettext("Only folders of your personal files are synced with the disk.")
        )
    result = SyncResult()

    storage_prefix = user_root(user.username)
    if folder is not None:
        storage_prefix = f"{storage_prefix}/{folder.path or folder.name}"

    if not default_storage.is_dir(storage_prefix):
        return result

    unsettled = relocations.journaled_paths()
    walk = _Walk(
        user,
        _NodeIndex.for_level(user, folder),
        dry_run,
        log or logger,
        result,
        unsettled,
    )
    _sync_level(walk, folder, storage_prefix)
    return result


def _sync_subtree(walk, parent_db, storage_prefix):
    """Sync one directory level, then recurse into subdirectories."""
    entries = walk.scan(storage_prefix)
    if entries is None:
        return

    renamed = _sync_level(walk, parent_db, storage_prefix, entries)

    live_here = walk.index.live_at(parent_db)
    for entry in entries:
        if not entry.is_dir:
            continue

        name = renamed.get(entry.name, entry.name)
        if walk.index.is_trashed(parent_db, name, File.NodeType.FOLDER):
            continue

        folder_db = live_here.get((name, File.NodeType.FOLDER))
        if folder_db:
            _sync_subtree(walk, folder_db, f"{storage_prefix}/{name}")


def _sync_level(walk, parent_db, storage_prefix, entries=None):
    """Sync the immediate children of one directory, both ways.

    Returns ``{name scanned: name given}`` for the disk entries it renamed.
    """
    now = timezone.now()

    if entries is None:
        entries = walk.scan(storage_prefix)
        if entries is None:
            return {}

    level = _Level(
        walk=walk,
        parent_db=parent_db,
        storage_prefix=storage_prefix,
        disk_names={entry.name: entry for entry in entries},
        rows=walk.index.live_at(parent_db),
        now=now,
    )
    # Rows first: a path that flipped from file to directory has to free its
    # name before the entry is adopted, or the row created for the new node
    # collides with the stale one.
    _settle_rows(level)
    _adopt_entries(level)
    return level.renamed


def _settle_rows(level):
    """DB -> disk: follow, respell or trash the rows the disk no longer holds."""
    for (name, node_type), db_record in list(level.rows.items()):
        if _held_on_disk(level, name, node_type):
            continue

        if level.walk.moving(f"{level.storage_prefix}/{db_record.name}"):
            continue  # its bytes may be on the other side of the move

        if name not in level.disk_names:
            new_name = level.spelled_otherwise(name, node_type)
            if new_name is not None:
                level.followed.add(new_name)
                if canonical_name(new_name) == name:
                    # The row's own name, decomposed on disk: names are
                    # stored composed, so the entry is respelled instead.
                    _respell_on_disk(level, new_name, name)
                else:
                    _follow_rename(level, db_record, new_name)
                continue

        # Not found on disk or type mismatch.
        _trash_row(level, db_record, name, node_type)


def _held_on_disk(level, name, node_type):
    """Whether the disk holds an entry named *name* of the row's type."""
    disk_entry = level.disk_names.get(name)
    if disk_entry is None:
        return False
    expected_type = File.NodeType.FOLDER if disk_entry.is_dir else File.NodeType.FILE
    return expected_type == node_type


def _count_trashed(result, node_type):
    if node_type == File.NodeType.FOLDER:
        result.folders_soft_deleted += 1
    else:
        result.files_soft_deleted += 1


def _trash_row(level, db_record, name, node_type):
    walk = level.walk
    if walk.dry_run:
        walk.log.info("[DRY-RUN] Would soft-delete %s: %s", node_type, scrub(name))
        _count_trashed(walk.result, node_type)
        walk.index.discard(db_record)
        return

    try:
        count = trash.soft_delete(
            db_record,
            acting_user=walk.user,
            deleted_at=level.now,
            details={"detected_by_sync": True},
        )
        walk.log.info("Soft-deleted %s: %s (%d records)", node_type, scrub(name), count)
        _count_trashed(walk.result, node_type)
        walk.index.discard(db_record)
    except Exception as e:
        level.untrashed.add(db_record.pk)
        walk.result.errors.append(f"Error soft-deleting {name}: {e}")
        walk.log.warning("Error soft-deleting %s: %s", scrub(name), scrub(e))


def _adopt_entries(level):
    """Disk -> DB: register the entries no row answers for."""
    walk = level.walk
    index = walk.index
    # By name, so that of two newcomers clashing caselessly the same one
    # keeps its name on every run, composed names first: a name written
    # composed keeps it, its decomposed twin is the newcomer.
    for entry_name, entry in sorted(
        level.disk_names.items(),
        key=lambda item: (item[0] != canonical_name(item[0]), item[0]),
    ):
        node_type = _node_type_of(entry)
        if node_type is None:
            continue  # skip symlinks, special files

        if entry_name in level.followed or (entry_name, node_type) in level.rows:
            continue  # already tracked

        if index.is_trashed(level.parent_db, entry_name, node_type):
            continue  # in trash, don't create a duplicate

        if walk.moving(f"{level.storage_prefix}/{entry.name}"):
            continue  # a copy or a source of a move: no file of its own

        name = canonical_name(entry_name)
        shortcut = None
        holder = index.holder_of(level.parent_db, entry_name)
        if holder is not None and holder.pk in level.untrashed:
            continue  # its name frees up once a later run trashes it
        if holder is not None and holder.node_type == File.NodeType.SHORTCUT:
            shortcut = holder
        elif holder is not None:
            taken = {
                *index.names_at(level.parent_db),
                *level.disk_names,
                *level.renamed.values(),
            }
            name = unique_copy_name(name, node_type, taken)

        if walk.dry_run:
            _report_adoption(level, entry_name, name, node_type, shortcut)
            continue

        try:
            _adopt(level, entry_name, name, node_type, shortcut, holder)
        except Exception as e:
            walk.result.errors.append(f"Error creating {entry_name}: {e}")
            walk.log.warning("Error creating %s: %s", scrub(entry_name), scrub(e))


def _report_adoption(level, entry_name, name, node_type, shortcut):
    """Count what adopting the entry would do, for a dry run."""
    log, result = level.walk.log, level.walk.result
    if shortcut is not None:
        log.info("[DRY-RUN] Would rename shortcut: %s", scrub(shortcut.name))
        result.shortcuts_renamed += 1
    if name != entry_name:
        log.info(
            "[DRY-RUN] Would rename on disk: %s -> %s",
            scrub(entry_name),
            scrub(name),
        )
        result.disk_entries_renamed += 1
    log.info("[DRY-RUN] Would create %s: %s", node_type, scrub(name))
    if node_type == File.NodeType.FOLDER:
        result.folders_created += 1
    else:
        result.files_created += 1


def _adopt(level, entry_name, name, node_type, shortcut, holder):
    """Register the disk entry *entry_name* as a row named *name*."""
    walk = level.walk
    user, log, result = walk.user, walk.log, walk.result
    if shortcut is not None:
        keep_free = {*level.disk_names, *level.renamed.values()}
        _rename_shortcut_aside(level, shortcut, keep_free)
    if name != entry_name:
        _rename_on_disk(level.storage_prefix, entry_name, name)
        level.renamed[entry_name] = name
        result.disk_entries_renamed += 1
        log.info(
            "Renamed on disk: %s -> %s, %s",
            scrub(entry_name),
            scrub(name),
            "a sibling holds the name" if holder else "names are composed",
        )
    if node_type == File.NodeType.FOLDER:
        created = tree.create_folder(user, name, level.parent_db, acting_user=user)
        walk.index.add(created)
        result.folders_created += 1
        log.info("Created folder: %s", scrub(name))
        return

    try:
        size = default_storage.size(f"{level.storage_prefix}/{name}")
    except OSError:
        size = None

    created = tree.register_disk_file(
        user,
        name,
        level.parent_db,
        f"{level.storage_prefix}/{name}",
        size=size,
        acting_user=user,
    )
    walk.index.add(created)
    result.files_created += 1
    log.info("Created file: %s (%s bytes)", scrub(name), size)


def _follow_rename(level, db_record, new_name):
    """Give the row the name its entry now has on disk."""
    walk = level.walk
    old_name = db_record.name
    if walk.dry_run:
        walk.log.info(
            "[DRY-RUN] Would follow rename: %s -> %s",
            scrub(old_name),
            scrub(new_name),
        )
        walk.result.renames_followed += 1
        return
    try:
        tree.rename(db_record, new_name, acting_user=walk.user)
    except Exception as e:
        # The rename rolled back, not the attributes it set on the row.
        db_record.refresh_from_db()
        walk.result.errors.append(f"Error renaming {old_name}: {e}")
        walk.log.warning("Error renaming %s: %s", scrub(old_name), scrub(e))
        return
    walk.index.renamed(db_record, old_name)
    walk.result.renames_followed += 1
    walk.log.info("Followed rename: %s -> %s", scrub(old_name), scrub(new_name))
    if db_record.name != new_name:
        # The row took the name composed; the entry follows it there.
        _respell_on_disk(level, new_name, db_record.name)


def _respell_on_disk(level, disk_name, name):
    """Move the entry *disk_name* under *name*, its composed spelling."""
    walk = level.walk
    if walk.dry_run:
        walk.log.info(
            "[DRY-RUN] Would rename on disk: %s -> %s",
            scrub(disk_name),
            scrub(name),
        )
        walk.result.disk_entries_renamed += 1
        return
    try:
        _rename_on_disk(level.storage_prefix, disk_name, name)
    except OSError as e:
        walk.result.errors.append(f"Error renaming {disk_name}: {e}")
        walk.log.warning("Error renaming %s: %s", scrub(disk_name), scrub(e))
        return
    level.renamed[disk_name] = name
    walk.result.disk_entries_renamed += 1
    walk.log.info(
        "Renamed on disk: %s -> %s, names are composed",
        scrub(disk_name),
        scrub(name),
    )


def _rename_shortcut_aside(level, shortcut, keep_free):
    walk = level.walk
    old_name = shortcut.name
    rename_aside(shortcut, keep_free=keep_free, acting_user=walk.user)
    walk.index.renamed(shortcut, old_name)
    walk.result.shortcuts_renamed += 1
    walk.log.info(
        "Renamed shortcut %s to %s: a disk entry holds its name",
        scrub(old_name),
        scrub(shortcut.name),
    )


def _rename_on_disk(storage_prefix, old_name, new_name):
    """Rename a disk entry, never replacing what holds *new_name*.

    Raises ``FileExistsError`` when something does, even something that
    appeared after the walk scanned the folder, where the backend can
    claim the name in the same step (``move_exclusive``).
    """
    default_storage.move_exclusive(
        f"{storage_prefix}/{old_name}", f"{storage_prefix}/{new_name}"
    )
