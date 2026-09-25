"""Write side of a VectorIndex: store a row's vector, drop it, rebuild the index.

The source column is always written; the derived structure (pgvector column,
vec0 row) is written alongside it when the backend has one, in the same
transaction, so the two never disagree about a row.

Imported from the submodule, not from the package: `workspace.common.vectors`
owns the read path and stays free of a dependency on this one.
"""

from django.db import DEFAULT_DB_ALIAS, connections, transaction

from . import active_backend, bind_uuid, sqlite_vec_available
from .encoding import from_bytes, normalize, pg_literal, to_bytes
from .postgres import PgvectorNearest, pgvector_available, vector_column_exists
from .sqlite import SqliteVecNearest

_REBUILD_BATCH = 500


def index_vector(index, pk, vector, *, using=DEFAULT_DB_ALIAS):
    """Store *vector* as row *pk*'s, normalized, and index it.

    Also re-derives the SQLite partition from the row, so call it again after
    changing a row's partition column. A row that no longer exists stores
    nothing - the pk is resolved by each statement.
    """
    stored = normalize(vector, index.dims)
    blob = to_bytes(stored)
    conn = connections[using]
    param = bind_uuid(pk, conn)
    with transaction.atomic(using=using), conn.cursor() as cursor:
        cursor.execute(index.update_source_sql(), [blob, param])
        # Only now, under the write lock: a rebuild creating the index holds
        # it until it commits, so a backend chosen before the lock was taken
        # would still see no index and leave this row out of the new one.
        backend = active_backend(index, conn)
        if isinstance(backend, PgvectorNearest):  # pragma: no cover - PG only
            cursor.execute(index.pg_set_vector_sql(), [pg_literal(stored), param])
        elif isinstance(backend, SqliteVecNearest):
            cursor.execute(index.sqlite_delete_sql(), [param])
            cursor.execute(index.sqlite_insert_sql(), [param])


def drop_vector(index, pk, *, using=DEFAULT_DB_ALIAS):
    """Remove row *pk*'s vector: the source and whatever is derived from it.

    On SQLite the vec0 table is a separate table, so a deleted row's entry
    stays there until this runs - before or after the delete, the entry is
    keyed on the pk alone. Call it from a pre_delete or post_delete receiver:
    an entry left behind never reaches a result, but costs the KNN a wider
    search until the next rebuild_vector_index purges it.
    """
    conn = connections[using]
    param = bind_uuid(pk, conn)
    with transaction.atomic(using=using), conn.cursor() as cursor:
        cursor.execute(index.update_source_sql(), [None, param])
        # Under the write lock, for the same reason as in index_vector.
        backend = active_backend(index, conn)
        if isinstance(backend, PgvectorNearest):  # pragma: no cover - PG only
            cursor.execute(index.pg_set_vector_sql(), [None, param])
        elif isinstance(backend, SqliteVecNearest):
            cursor.execute(index.sqlite_delete_sql(), [param])


def rebuild_vector_index(index, *, using=DEFAULT_DB_ALIAS):
    """Recreate *index*'s derived structure from the source column.

    Returns the backend now serving the index ("pgvector", "sqlite-vec" or
    "numpy"). Creates the structure when the migration could not - pgvector or
    sqlite-vec installed after the fact - and purges entries left behind by
    rows deleted without drop_vector. Nothing to do on the fallback, which
    reads the source column directly.
    """
    conn = connections[using]
    if conn.vendor == "postgresql":  # pragma: no cover - exercised on PG only
        if pgvector_available(conn):
            # One transaction: the column is dropped and refilled under an
            # exclusive lock, so a concurrent search waits instead of finding
            # an empty index.
            with transaction.atomic(using=using):
                _run_script(conn, index.pg_forward_sql())
                if vector_column_exists(conn, index):
                    _backfill_pg(index, conn)
    elif conn.vendor == "sqlite":
        if sqlite_vec_available(conn):
            with transaction.atomic(using=using):
                _run_script(conn, index.sqlite_forward_sql())
    return active_backend(index, conn).name


def _backfill_pg(index, conn):  # pragma: no cover - exercised on PG only
    """Derive the vector column from the source, one keyset page at a time."""
    after = None
    while True:
        with conn.cursor() as cursor:
            cursor.execute(
                index.pg_source_rows_page_sql(), [after, after, _REBUILD_BATCH]
            )
            rows = cursor.fetchall()
        if not rows:
            return
        batch = []
        for pk, blob in rows:
            vector = from_bytes(blob, index.dims)
            if vector is not None:
                batch.append([pg_literal(vector), pk])
        with conn.cursor() as cursor:
            cursor.executemany(index.pg_set_vector_sql(), batch)
        after = rows[-1][0]


def _run_script(conn, sql):
    """Run a multi-statement script one statement at a time.

    Never executescript(): on SQLite it commits whatever transaction is open
    first, which would break the caller's atomicity (and a test's isolation).
    PostgreSQL takes the script whole, which keeps a DO $$ ... $$ block intact.
    """
    with conn.cursor() as cursor:
        if conn.vendor == "postgresql":  # pragma: no cover - exercised on PG only
            cursor.execute(sql)
            return
        for statement in conn.ops.prepare_sql_script(sql):
            cursor.execute(statement)
