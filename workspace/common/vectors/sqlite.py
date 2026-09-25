"""sqlite-vec: loading the extension, probing for it, and KNN over vec0."""

import logging
import sqlite3

from django.db import OperationalError

from .encoding import to_bytes

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
        params = [to_bytes(query), k]
        if index.partitioned:
            params.append(partition)
        with conn.cursor() as cursor:
            cursor.execute(index.sqlite_nearest_sql(), params)
            return cursor.fetchall()
