"""sqlite-vec: loading the extension, probing for it, and KNN over vec0."""

import logging
import math
import sqlite3

from django.db import OperationalError

from .encoding import to_bytes
from .schema import MAX_K

logger = logging.getLogger(__name__)

_load_failure_logged = False


def load_into(raw_connection):
    """Load sqlite-vec into a DB-API sqlite3 connection. Raises when it can't.

    AttributeError: a Python built without extension loading (some system
    interpreters). ImportError: the wheel is not installed for this platform.
    sqlite3.Error: the library itself refused to load.
    """
    import sqlite_vec

    raw_connection.enable_load_extension(True)
    try:
        sqlite_vec.load(raw_connection)
    finally:
        raw_connection.enable_load_extension(False)


def load_sqlite_vec(sender, connection, **kwargs):
    """connection_created receiver: every new SQLite connection gets sqlite-vec.

    Fails soft - vector search falls back to a scan of the source column, and
    the common.W002 check says why.
    """
    global _load_failure_logged
    if connection.vendor != "sqlite":
        return
    try:
        load_into(connection.connection)
    except (AttributeError, ImportError, sqlite3.Error) as exc:
        if not _load_failure_logged:
            _load_failure_logged = True
            logger.info(
                "sqlite-vec not loaded, vector search falls back to numpy: %s", exc
            )


def sqlite_vec_loadable():
    """Whether sqlite-vec loads in this interpreter, tried on a scratch database.

    Touches no configured database, so the system check can call it at any time.
    """
    raw = sqlite3.connect(":memory:")
    try:
        load_into(raw)
    except AttributeError, ImportError, sqlite3.Error:
        return False
    finally:
        raw.close()
    return True


def sqlite_vec_loaded(conn):
    """Whether sqlite-vec is loaded on *conn*. Uncached."""
    with conn.cursor() as cursor:
        try:
            cursor.execute("SELECT vec_version()")
        except OperationalError:
            return False
    return True


def vec_table_exists(conn, index):
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = %s",
            [index.vec_table],
        )
        return cursor.fetchone() is not None


class SqliteVecNearest:
    name = "sqlite-vec"

    def nearest(self, index, conn, query, *, partition, k):
        """The k nearest live rows, widening the KNN past dead entries.

        An entry outlives its row when the row goes without drop_vector (a
        data migration deleting through apps.get_model, a process that could
        not load sqlite-vec). Such entries are, by construction, often the
        query's nearest neighbours, so a KNN of exactly k could come back
        empty with k live rows left: while dead entries crowd live ones out,
        ask for twice as many, up to MAX_K.
        """
        blob = to_bytes(query)
        fetch = k
        while True:
            params = [blob, fetch]
            if index.partitioned:
                params.append(partition)
            with conn.cursor() as cursor:
                cursor.execute(index.sqlite_nearest_sql(), params)
                entries = cursor.fetchall()
            # A zero vector's cosine distance is NaN, which SQLite returns as
            # NULL and sorts first: it ranks last instead, as in the fallback.
            live = sorted(
                (
                    (pk, math.inf if distance is None else distance)
                    for pk, distance, alive in entries
                    if alive
                ),
                key=lambda entry: entry[1],
            )
            if len(live) >= k or len(entries) < fetch or fetch == MAX_K:
                return live[:k]
            fetch = min(fetch * 2, MAX_K)
