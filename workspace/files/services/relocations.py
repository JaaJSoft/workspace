"""A journal of the moves storage has not finished, and what settles them.

A move renames its blobs (on a disk) or copies them (on object storage) inside
the transaction that repoints the rows, and drops what is left at the source
once that transaction commits. A worker that dies in between, or a transaction
that rolls back after the move went through, leaves the bytes and the rows on
different sides: copies no row points at, sources the move should have
dropped, or - on a disk, where a rename is final - a folder the rolled-back
rows still look for where it was.

So every move writes a journal entry before it touches a blob and removes it
once nothing of it is left to drop. An entry still there long after any
transaction could have ended belongs to a move that never finished, and
settling it puts every blob back on the side its row points at. Until then the
sync leaves both sides alone: a copy or a source it adopted would come back as
a second file.
"""

import json
import logging
import posixpath
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import batched

from django.core.exceptions import SuspiciousFileOperation
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

from workspace.common.logging import scrub

from ..models import File

logger = logging.getLogger(__name__)

# Under the storage root, outside every tree the sync, copy_blobs and
# verify_file_storage walk.
JOURNAL_DIR = ".relocations"

# Far longer than any transaction holding a move stays open. Settling an entry
# sooner could act on rows that have not committed yet: the copies would look
# like nobody's.
SETTLE_AFTER = timedelta(days=1)

_QUERY_BATCH = 500


@dataclass(frozen=True)
class Entry:
    """A journaled move: where from, where to, and when it was written."""

    name: str
    source: str
    destination: str
    written: datetime


@dataclass
class Settled:
    """What settling the stale entries did."""

    entries: int = 0
    dropped: list = field(default_factory=list)
    restored: list = field(default_factory=list)
    undecided: list = field(default_factory=list)


def begin(source, destination):
    """Journal a move of *source* to *destination*; returns the entry's name."""
    name = posixpath.join(JOURNAL_DIR, f"{uuid.uuid4().hex}.json")
    body = json.dumps({"source": source, "destination": destination})
    default_storage.replace(name, ContentFile(body.encode()))
    return name


def end(name):
    """Remove a journal entry; one left behind is settled later, harmlessly."""
    try:
        default_storage.delete(name)
    except OSError as exc:
        logger.warning("Could not remove journal entry %s: %s", scrub(name), scrub(exc))


def entries():
    """Every journaled move, oldest first."""
    if not default_storage.is_dir(JOURNAL_DIR):
        return []
    found = []
    for blob in default_storage.iter_blobs(JOURNAL_DIR):
        try:
            with default_storage.open(blob.name, "rb") as handle:
                body = json.loads(handle.read())
            found.append(
                Entry(blob.name, body["source"], body["destination"], blob.modified)
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning(
                "Unreadable journal entry %s: %s", scrub(blob.name), scrub(exc)
            )
    return sorted(found, key=lambda entry: entry.written)


def journaled_paths():
    """The source and destination of every journaled move."""
    return {path for entry in entries() for path in (entry.source, entry.destination)}


def covers(paths, name):
    """Whether *name* is one of *paths* or lies under one of them."""
    return any(name == path or name.startswith(f"{path}/") for path in paths)


def settle_stale(*, older_than=SETTLE_AFTER, now=None):
    """Settle every entry written more than *older_than* ago, then remove it."""
    cutoff = (now or timezone.now()) - older_than
    result = Settled()
    for entry in entries():
        if entry.written >= cutoff:
            continue
        try:
            settle(entry, result)
        except SuspiciousFileOperation:
            # A path no storage verb accepts never settles: keeping the entry
            # would only refuse the same request every time.
            logger.warning("Dropped journal entry %s: unsafe path", scrub(entry.name))
        except OSError as exc:
            logger.warning("Could not settle %s: %s", scrub(entry.name), scrub(exc))
            continue
        end(entry.name)
        result.entries += 1
    return result


def settle(entry, result):
    """Put every blob of *entry*'s move on the side its row points at.

    Blob by blob, a source and its destination are a pair. When exactly one
    of them is a row's blob, the other is a leftover: dropped when the row's
    side holds the bytes too, moved there when it does not - a disk renames,
    so after a rollback the destination holds the only copy. A pair that no
    row, or two rows, point at is left alone: nothing says which side is the
    right one.
    """
    storage = default_storage
    folder_pairs, folders_found = _directory_pairs(entry.source, entry.destination)
    pairs = _pairs(entry.source, entry.destination)
    referenced = _referenced([name for pair in pairs for name in pair[:2]])
    for source, destination, source_exists, destination_exists in pairs:
        if (source in referenced) == (destination in referenced):
            if source not in referenced:
                # Nobody's bytes: listed, never deleted on a guess.
                result.undecided.extend(
                    name
                    for name, exists in (
                        (source, source_exists),
                        (destination, destination_exists),
                    )
                    if exists
                )
            continue
        if source in referenced:
            keep, keep_exists, drop, drop_exists = (
                source,
                source_exists,
                destination,
                destination_exists,
            )
        else:
            keep, keep_exists, drop, drop_exists = (
                destination,
                destination_exists,
                source,
                source_exists,
            )
        if not drop_exists:
            continue
        if keep_exists:
            storage.delete(drop)
            result.dropped.append(drop)
        else:
            storage.move(drop, keep)
            result.restored.append(keep)
    # An empty folder has no blob to carry it back: it gets its directory
    # where its row is, when the move had taken it to the other side.
    for source, destination in folder_pairs:
        for keep, drop in ((source, destination), (destination, source)):
            if drop in folders_found and keep not in folders_found:
                if _folder_at(keep):
                    storage.make_dir(keep)
    for side in (entry.source, entry.destination):
        _prune_directories(side)


def _pairs(source, destination):
    """``(source blob, destination blob, source exists, destination exists)``."""
    storage = default_storage
    if storage.is_file(source) or storage.is_file(destination):
        return [
            (source, destination, storage.is_file(source), storage.is_file(destination))
        ]
    sources = _suffixes(source)
    destinations = _suffixes(destination)
    return [
        (
            source + suffix,
            destination + suffix,
            suffix in sources,
            suffix in destinations,
        )
        for suffix in sorted(sources | destinations)
    ]


def _directory_pairs(source, destination):
    """The directory pairs of a folder's move, and the directories found.

    Read before any blob moves: a blob moved back recreates its parents.
    """
    storage = default_storage
    sides = {}
    for top in (source, destination):
        found = set()
        if storage.is_dir(top):
            found.add("")
            found.update(name[len(top) :] for name in storage.iter_dirs(top))
        sides[top] = found
    pairs = [
        (source + suffix, destination + suffix)
        for suffix in sorted(sides[source] | sides[destination])
    ]
    found = {top + suffix for top, suffixes in sides.items() for suffix in suffixes}
    return pairs, found


def _suffixes(directory):
    if not default_storage.is_dir(directory):
        return set()
    return {
        blob.name[len(directory) :] for blob in default_storage.iter_blobs(directory)
    }


def _referenced(names):
    """Of *names*, the ones a row - live or trashed - keeps its bytes under."""
    found = set()
    for batch in batched(dict.fromkeys(names), _QUERY_BATCH, strict=False):
        found.update(
            File.objects.filter(content__in=batch).values_list("content", flat=True)
        )
    return found


def _prune_directories(top):
    """Remove the empty directories under *top* (and *top*) no folder holds."""
    storage = default_storage
    if not storage.is_dir(top):
        return
    directories = [*storage.iter_dirs(top), top]
    for directory in sorted(directories, key=lambda d: d.count("/"), reverse=True):
        if not _folder_at(directory):
            storage.remove_dir_if_empty(directory)


def _folder_at(directory):
    """Whether a folder row's bytes belong at storage directory *directory*.

    A directory above every node - a storage root, a user's - always is.
    """
    parts = directory.split("/")
    folders = File.objects.filter(node_type=File.NodeType.FOLDER)
    if parts[:2] == ["files", "users"]:
        if len(parts) < 4:
            return True
        return folders.filter(
            owner__username=parts[2],
            group__isnull=True,
            path="/".join(parts[3:]),
            deleted_at__isnull=True,
        ).exists()
    if parts[:2] == ["files", "groups"]:
        if len(parts) < 3:
            return True
        return folders.filter(
            group__isnull=False, path="/".join(parts[2:]), deleted_at__isnull=True
        ).exists()
    if parts[:2] in (["trash", "users"], ["trash", "groups"]):
        # trash/users/<username>/<uuid>/<name>/... or trash/groups/<uuid>/<name>/...
        at = 3 if parts[1] == "users" else 2
        if len(parts) <= at:
            return True
        root = File.objects.filter(
            pk=_uuid(parts[at]), deleted_at__isnull=False
        ).first()
        if root is None:
            return False
        if len(parts) == at + 1:
            return True
        if len(parts) == at + 2:
            return root.node_type == File.NodeType.FOLDER
        same_tree = (
            {"group": root.group_id}
            if root.group_id
            else {"owner": root.owner_id, "group__isnull": True}
        )
        return folders.filter(
            path="/".join([root.path, *parts[at + 2 :]]),
            deleted_at__isnull=False,
            **same_tree,
        ).exists()
    return True


def _uuid(value):
    try:
        return uuid.UUID(value)
    except ValueError:
        return None
