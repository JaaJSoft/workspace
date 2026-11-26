"""SQLite upkeep: the scheduled pass, the full VACUUM and snapshots.

SQLite takes one writer at a time, so what matters here is how long each step
holds the write lock. Writers wait for it up to the busy timeout (60 s, see
``settings/db.py``) and then fail with "database is locked".

- The scheduled pass (``run_maintenance``) returns free pages to the
  filesystem with ``PRAGMA incremental_vacuum``, in steps of a few thousand
  pages, each one its own short write transaction.
- ``full_vacuum`` rewrites the whole file and holds the write lock while it
  runs. Nothing schedules it: it is the one-off step that switches an existing
  database to incremental mode, run by hand through ``manage.py db_maintenance
  --full-vacuum``.
- ``snapshot`` writes a compacted copy with ``VACUUM INTO``, from a read
  transaction, so writers keep going while it runs.
"""

import logging
import os
import tempfile
import time
from collections import defaultdict
from shutil import disk_usage

from django.db import connection as default_connection

logger = logging.getLogger(__name__)

AUTO_VACUUM_MODES = {0: "none", 1: "full", 2: "incremental"}

# One step frees this many pages in a single write transaction: 4 MB with the
# default 4 KB pages, tens of milliseconds on a local disk.
INCREMENTAL_STEP_PAGES = 1000
# Between two steps the lock is free for longer than the longest sleep of
# SQLite's busy handler (100 ms), so a writer waiting for it gets in.
INCREMENTAL_PAUSE_SECONDS = 0.15
# A daily run stops after this long and leaves the rest for the next one.
INCREMENTAL_BUDGET_SECONDS = 60.0

# Room left on top of the estimate: the database keeps growing while a copy
# is written, and a disk filled to the last byte breaks more than the copy.
FREE_SPACE_MARGIN = 1.1


class NotEnoughSpace(Exception):
    """A copy of the database would not fit on the disk it is written to."""


def is_sqlite(db=None):
    return (db or default_connection).vendor == "sqlite"


def _pragma(db, name):
    with db.cursor() as cursor:
        cursor.execute(f"PRAGMA {name};")
        return cursor.fetchone()[0]


def _elapsed_ms(start):
    return round((time.monotonic() - start) * 1000, 1)


def _file_size(db):
    try:
        return os.path.getsize(db.settings_dict["NAME"])
    except OSError:
        return None


def database_stats(db=None):
    """Page counts and the auto_vacuum mode the file is in."""
    db = db or default_connection
    page_size = _pragma(db, "page_size")
    page_count = _pragma(db, "page_count")
    freelist = _pragma(db, "freelist_count")
    return {
        "auto_vacuum": AUTO_VACUUM_MODES.get(_pragma(db, "auto_vacuum"), "unknown"),
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist,
        "used_bytes": (page_count - freelist) * page_size,
        "file_size": _file_size(db),
    }


def optimize(db=None):
    db = db or default_connection
    start = time.monotonic()
    with db.cursor() as cursor:
        cursor.execute("PRAGMA optimize;")
    return {"optimize_ms": _elapsed_ms(start)}


def wal_checkpoint(db=None):
    """Copy the WAL into the database and truncate it.

    In WAL mode this is also where the pages an incremental vacuum freed leave
    the file: the database is truncated when the commit that shrank it is
    checkpointed.
    """
    db = db or default_connection
    start = time.monotonic()
    with db.cursor() as cursor:
        cursor.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        row = cursor.fetchone()
    return {
        "wal_checkpoint": {
            "return_code": row[0],
            "pages_written": row[1],
            "pages_checkpointed": row[2],
        },
        "wal_checkpoint_ms": _elapsed_ms(start),
    }


def incremental_vacuum(
    db=None,
    *,
    step_pages=INCREMENTAL_STEP_PAGES,
    pause_seconds=INCREMENTAL_PAUSE_SECONDS,
    budget_seconds=INCREMENTAL_BUDGET_SECONDS,
):
    """Return free pages to the filesystem, a bounded step at a time.

    Does nothing on a database still in ``auto_vacuum = NONE``: its free pages
    only leave the file through ``full_vacuum``. Always runs at least one step
    when there are free pages, then stops once the budget is spent.
    """
    db = db or default_connection
    if db.in_atomic_block:
        # Every step would hold the write lock until the transaction ends.
        raise RuntimeError("incremental_vacuum must run in autocommit")

    mode = AUTO_VACUUM_MODES.get(_pragma(db, "auto_vacuum"), "unknown")
    result = {"auto_vacuum": mode, "pages_freed": 0, "steps": 0}
    if mode != "incremental":
        result["freelist_count"] = _pragma(db, "freelist_count")
        return result

    start = time.monotonic()
    deadline = start + budget_seconds
    free = _pragma(db, "freelist_count")
    while free:
        with db.cursor() as cursor:
            # The pragma frees one page per step of the statement: it only
            # runs to the end once every row is fetched, and until then it
            # keeps the statement - and the write lock - open.
            cursor.execute(f"PRAGMA incremental_vacuum({int(step_pages)});")
            cursor.fetchall()
        result["steps"] += 1
        remaining = _pragma(db, "freelist_count")
        result["pages_freed"] += free - remaining
        free = remaining
        if not free or time.monotonic() + pause_seconds >= deadline:
            break
        time.sleep(pause_seconds)

    result["freelist_count"] = free
    result["incremental_vacuum_ms"] = _elapsed_ms(start)
    return result


def integrity_check(db=None):
    """Read the whole database and report what is inconsistent.

    A read transaction: writers keep going, but checkpoints cannot pass its
    snapshot, so the WAL grows while it runs.
    """
    db = db or default_connection
    start = time.monotonic()
    with db.cursor() as cursor:
        cursor.execute("PRAGMA integrity_check;")
        rows = cursor.fetchall()
    return {
        "integrity_check": rows[0][0] if rows else "unknown",
        "integrity_check_ms": _elapsed_ms(start),
    }


def sqlite_temp_dir():
    """The directory SQLite writes its temporary databases to.

    The order of ``unixTempFileDir`` in SQLite's ``os_unix.c``. A full VACUUM
    builds its new copy of the database there (``temp_store=FILE``).
    """
    if os.name == "nt":
        return tempfile.gettempdir()
    candidates = (
        os.environ.get("SQLITE_TMPDIR"),
        os.environ.get("TMPDIR"),
        "/var/tmp",
        "/usr/tmp",
        "/tmp",
    )
    for path in candidates:
        if path and os.path.isdir(path) and os.access(path, os.W_OK | os.X_OK):
            return path
    return "."


def ensure_free_space(needs):
    """Raise ``NotEnoughSpace`` unless each disk can take what is written to it.

    ``needs`` maps a directory to the bytes written there. Directories on the
    same filesystem add up.
    """
    dirs_by_device = defaultdict(list)
    needed_by_device = defaultdict(int)
    for directory, needed in needs.items():
        device = os.stat(directory).st_dev
        dirs_by_device[device].append(directory)
        needed_by_device[device] += needed

    for device, dirs in dirs_by_device.items():
        needed = int(needed_by_device[device] * FREE_SPACE_MARGIN)
        free = disk_usage(dirs[0]).free
        if free < needed:
            raise NotEnoughSpace(
                f"{', '.join(map(str, dirs))}: {_megabytes(needed)} needed, "
                f"{_megabytes(free)} free"
            )


def _megabytes(size):
    return f"{size / 2**20:.0f} MB"


def full_vacuum(db=None):
    """Rewrite the database file, and switch it to incremental auto_vacuum.

    Holds the write lock for the whole rewrite. It writes a new copy of the
    database to SQLite's temporary directory, then the copy again through the
    WAL next to the database, so it refuses to start without room for both.
    """
    db = db or default_connection
    db.ensure_connection()
    before = database_stats(db)
    database_dir = os.path.dirname(os.path.abspath(db.settings_dict["NAME"]))
    ensure_free_space(
        {
            sqlite_temp_dir(): before["used_bytes"],
            database_dir: before["used_bytes"],
        }
    )

    start = time.monotonic()
    with db.cursor() as cursor:
        # Only a VACUUM on this connection, with this set, switches a
        # database created in auto_vacuum = NONE.
        cursor.execute("PRAGMA auto_vacuum = INCREMENTAL;")
        cursor.execute("VACUUM;")
    result = {"vacuum_ms": _elapsed_ms(start)}
    result.update(wal_checkpoint(db))

    after = database_stats(db)
    result.update(
        {
            "auto_vacuum_before": before["auto_vacuum"],
            "auto_vacuum": after["auto_vacuum"],
            "size_before": before["file_size"],
            "size_after": after["file_size"],
        }
    )
    return result


def snapshot(destination, db=None):
    """Write a compacted copy of the database to ``destination``.

    ``VACUUM INTO`` reads from a single read transaction: the copy is
    consistent, and writers are not held up while it is written.
    """
    db = db or default_connection
    destination = os.path.abspath(destination)
    if os.path.exists(destination):
        raise FileExistsError(destination)
    directory = os.path.dirname(destination)
    if not os.path.isdir(directory):
        raise FileNotFoundError(directory)

    db.ensure_connection()
    ensure_free_space({directory: database_stats(db)["used_bytes"]})
    start = time.monotonic()
    with db.cursor() as cursor:
        cursor.execute("VACUUM INTO %s;", [destination])
    return {
        "path": destination,
        "size": os.path.getsize(destination),
        "snapshot_ms": _elapsed_ms(start),
    }


def run_maintenance(db=None, *, with_integrity_check=False):
    """The scheduled pass: no step holds the write lock for long.

    ``PRAGMA optimize``, the bounded incremental vacuum, a WAL checkpoint that
    hands the freed pages back to the filesystem, then optionally an integrity
    check.
    """
    db = db or default_connection
    if not is_sqlite(db):
        return {"skipped": True, "reason": "not SQLite"}

    db.ensure_connection()
    result = {"size_before": _file_size(db)}
    result.update(optimize(db))
    result.update(incremental_vacuum(db))
    if result["auto_vacuum"] != "incremental":
        logger.warning(
            "SQLite auto_vacuum is %s: free pages stay in the file until "
            "'manage.py db_maintenance --full-vacuum' switches it to incremental",
            result["auto_vacuum"],
        )
    result.update(wal_checkpoint(db))
    result["size_after"] = _file_size(db)
    if with_integrity_check:
        result.update(integrity_check(db))
        if result["integrity_check"] != "ok":
            logger.warning(
                "SQLite integrity check reported issues: %s",
                result["integrity_check"],
            )
    return result
