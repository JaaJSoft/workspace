"""Read side of a VectorIndex: the k nearest rows to a vector.

Not a queryset: a KNN wants to be the top-level query on both engines (vec0's
MATCH ... AND k = ?, pgvector's ORDER BY ... LIMIT). The caller re-enters the
ORM with the returned primary keys (``pk__in``), which is where access control
applies - the partition narrows the search, it does not authorize it.
"""

from django.db import DEFAULT_DB_ALIAS, connections

from workspace.common.uuids import parse_uuid_or_none

from .encoding import normalize
from .fallback import NumpyNearest
from .postgres import PgvectorNearest, pgvector_version, vector_column_exists
from .schema import MAX_K
from .sqlite import SqliteVecNearest, sqlite_vec_loaded, vec_table_exists

# Per connection alias. Only the extension probes are cached: whether an index's
# own table or column exists is asked on every call, because a rebuild run from
# another process can create it at any time, and a process that went on
# believing it absent would stop feeding it.
_sqlite_vec_cache = {}
_pgvector_version_cache = {}


def nearest(index, query, *, partition=None, k, using=DEFAULT_DB_ALIAS):
    """The *k* rows of *index* nearest to *query*, as [(pk, distance)].

    Closest first. *partition* is mandatory on a partitioned index and refused
    on another one: a forgotten partition would silently search every user's
    rows. Rows without a vector are never returned.
    """
    if index.partitioned and partition is None:
        raise ValueError(f"{index.table}: a partitioned vector index needs a partition")
    if not index.partitioned and partition is not None:
        raise ValueError(f"{index.table}: this vector index has no partition")
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= MAX_K:
        raise ValueError(f"k must be an integer between 1 and {MAX_K}, got {k!r}")
    vector = normalize(query, index.dims)
    conn = connections[using]
    if partition is not None:
        partition = _bind_partition(partition, conn)
    rows = active_backend(index, conn).nearest(
        index, conn, vector, partition=partition, k=k
    )
    return [(_as_pk(pk), float(distance)) for pk, distance in rows]


def active_backend(index, conn):
    """The backend serving *index* on *conn* right now."""
    if conn.vendor == "postgresql":  # pragma: no cover - exercised on PG only
        if vector_column_exists(conn, index):
            return PgvectorNearest(version=_pgvector_version(conn) or ())
    elif conn.vendor == "sqlite":
        if sqlite_vec_available(conn) and vec_table_exists(conn, index):
            return SqliteVecNearest()
    return NumpyNearest()


def sqlite_vec_available(conn):
    """Whether sqlite-vec is loaded on *conn*'s database (cached per alias)."""
    cached = _sqlite_vec_cache.get(conn.alias)
    if cached is None:
        cached = _sqlite_vec_cache[conn.alias] = sqlite_vec_loaded(conn)
    return cached


def _pgvector_version(conn):  # pragma: no cover - exercised on PG only
    cached = _pgvector_version_cache.get(conn.alias)
    if cached is None:
        cached = pgvector_version(conn)
        if cached is not None:
            _pgvector_version_cache[conn.alias] = cached
    return cached


def bind_uuid(value, conn):
    """A UUID bound the way *conn* stores it (char(32) hex on SQLite)."""
    parsed = parse_uuid_or_none(value)
    if parsed is None:
        raise ValueError(f"not a UUID: {value!r}")
    return parsed if conn.features.has_native_uuid_field else parsed.hex


def _bind_partition(value, conn):
    parsed = parse_uuid_or_none(value)
    if parsed is None:
        return value
    return parsed if conn.features.has_native_uuid_field else parsed.hex


def _as_pk(value):
    """Primary keys come back as UUIDs whatever the backend stores."""
    return parse_uuid_or_none(value) or value
