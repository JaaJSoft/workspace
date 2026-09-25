"""Write side of a VectorIndex: store a row's vector, drop it, rebuild the index.

The source column is always written; the derived structure (pgvector column,
vec0 row) is written alongside it when the backend has one, in the same
transaction, so the two never disagree about a row.

Imported from the submodule, not from the package: `workspace.common.vectors`
owns the read path and stays free of a dependency on this one.
"""

from django.db import DEFAULT_DB_ALIAS, connections, transaction

from workspace.common.rowids import adapt_pk, claim_rowid

from . import active_backend, sqlite_vec_available
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
    param = adapt_pk(pk, conn)
    backend = active_backend(index, conn)
    with transaction.atomic(using=using), conn.cursor() as cursor:
        if isinstance(backend, PgvectorNearest):  # pragma: no cover - PG only
            cursor.execute(index.pg_update_sql(), [blob, pg_literal(stored), param])
            return
        cursor.execute(index.update_source_sql(), [blob, param])
        if isinstance(backend, SqliteVecNearest):
            _reindex_sqlite_row(index, cursor, pk, param)


def drop_vector(index, pk, *, using=DEFAULT_DB_ALIAS):
    """Remove row *pk*'s vector: the source and whatever is derived from it.

    On SQLite the vec0 table is a separate table, so call this while the row
    still exists when the row itself is about to be deleted - the key is read
    off it. An entry left behind is harmless to results (the KNN joins back to
    live rows) but takes a slot among the k until the next rebuild.
    """
    conn = connections[using]
    param = adapt_pk(pk, conn)
    backend = active_backend(index, conn)
    with transaction.atomic(using=using), conn.cursor() as cursor:
        if isinstance(backend, PgvectorNearest):  # pragma: no cover - PG only
            cursor.execute(index.pg_clear_sql(), [param])
            return
        if isinstance(backend, SqliteVecNearest):
            rowid = _read_rowid(index, cursor, param)
            if rowid is not None:
                cursor.execute(index.sqlite_delete_sql(), [rowid])
        cursor.execute(index.update_source_sql(), [None, param])


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
            _claim_missing_rowids(index, conn)
            with transaction.atomic(using=using):
                _run_script(conn, index.sqlite_forward_sql())
    return active_backend(index, conn).name


def _reindex_sqlite_row(index, cursor, pk, param):
    if not claim_rowid(
        cursor,
        read_sql=index.sqlite_read_rowid_sql(),
        assign_sql=index.sqlite_assign_rowid_sql(),
        pk=pk,
        param=param,
    ):
        return
    rowid = _read_rowid(index, cursor, param)
    cursor.execute(index.sqlite_delete_sql(), [rowid])
    cursor.execute(index.sqlite_insert_sql(), [param])


def _read_rowid(index, cursor, param):
    cursor.execute(index.sqlite_read_rowid_sql(), [param])
    row = cursor.fetchone()
    return None if row is None else row[0]


def _claim_missing_rowids(index, conn):
    with conn.cursor() as cursor:
        cursor.execute(index.sqlite_unkeyed_pks_sql())
        pks = [row[0] for row in cursor.fetchall()]
    for pk in pks:
        with transaction.atomic(using=conn.alias), conn.cursor() as cursor:
            claim_rowid(
                cursor,
                read_sql=index.sqlite_read_rowid_sql(),
                assign_sql=index.sqlite_assign_rowid_sql(),
                pk=pk,
                param=pk,
            )


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
