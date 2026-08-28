"""Write side of a DerivedFulltextIndex: push one document into the index.

The text is always a statement parameter, never inlined and never stored in a
column: PostgreSQL keeps the resulting tsvector, SQLite the FTS5 postings.
Callers own the extraction; this module only knows how to hand a document to
whichever backend is active.

Imported from the submodule, not from the package: `workspace.common.search`
owns the read path and stays free of a dependency on this one.
"""

from django.db import DEFAULT_DB_ALIAS, IntegrityError, connections, transaction

from workspace.common.search import fts5_available
from workspace.common.uuids import parse_uuid_or_none

# FTS5 rowids are signed 64-bit, so the key is the low 63 bits of the UUID.
_ROWID_MASK = (1 << 63) - 1
_ROWID_ATTEMPTS = 4


def document_values(index, values):
    """Field values in declaration order, stringified, capped, never None."""
    texts = []
    for field in index.fields:
        text = values.get(field.name)
        text = "" if text is None else str(text)
        if field.cap is not None:
            text = text[: field.cap]
        texts.append(text)
    return texts


def index_document(index, pk, values, *, using=DEFAULT_DB_ALIAS):
    """(Re)write the indexed document for row *pk* of *index*'s table.

    A row that no longer exists indexes nothing - the pk is resolved by the
    statement itself, so a concurrent delete cannot leave an orphan behind.
    """
    conn = connections[using]
    param = _adapt_pk(pk, conn)
    texts = document_values(index, values)
    if conn.vendor == "postgresql":  # pragma: no cover - exercised on PG only
        with conn.cursor() as cursor:
            cursor.execute(index.pg_update_sql(), [*texts, param])
        return
    if conn.vendor == "sqlite" and fts5_available():
        with conn.cursor() as cursor:
            if index.rowid_column and not _ensure_rowid(index, pk, param, cursor):
                return
            cursor.execute(index.sqlite_delete_sql(), [param])
            cursor.execute(index.sqlite_insert_sql(), [*texts, param])


def drop_document(index, pk, *, using=DEFAULT_DB_ALIAS):
    """Remove row *pk*'s document from the index.

    Only SQLite needs this: its contentless table is a separate table, so it
    must be told the row is going away - and told while the row still exists,
    since the key is read off it. On PostgreSQL the tsvector is a column of
    the row and dies with it, so this is deliberately a no-op there rather
    than a wasted UPDATE per deleted row.
    """
    conn = connections[using]
    if conn.vendor == "sqlite" and fts5_available():
        with conn.cursor() as cursor:
            cursor.execute(index.sqlite_delete_sql(), [_adapt_pk(pk, conn)])


def _ensure_rowid(index, pk, param, cursor):
    """Give the row a stable FTS key, once. True when it has one.

    FTS5 rowids are integers and the key has to survive a table rebuild, so it
    is derived from the UUID rather than taken from a sequence: deterministic,
    needs no coordination between concurrent indexing tasks, and stays put
    when Django copies the table. Uniqueness is still the database's call -
    the column carries a unique constraint - so a collision (about 62 bits of
    entropy) surfaces as an IntegrityError rather than two files quietly
    sharing one index entry, and the next candidate is tried.
    """
    cursor.execute(index.sqlite_read_rowid_sql(), [param])
    row = cursor.fetchone()
    if row is None:
        return False  # the row is gone; nothing to index
    if row[0] is not None:
        return True

    parsed = parse_uuid_or_none(pk)
    if parsed is None:
        return False
    for attempt in range(_ROWID_ATTEMPTS):
        candidate = (parsed.int + attempt) & _ROWID_MASK
        try:
            with transaction.atomic(using=cursor.db.alias):
                cursor.execute(index.sqlite_assign_rowid_sql(), [candidate, param])
        except IntegrityError:
            continue
        return True
    return False


def _adapt_pk(pk, conn):
    """Bind a UUID the way the backend stores it (char(32) hex on SQLite)."""
    parsed = parse_uuid_or_none(pk)
    if parsed is None:
        return pk
    return parsed if conn.features.has_native_uuid_field else parsed.hex
