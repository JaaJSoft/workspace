"""Write side of a DerivedFulltextIndex: push one document into the index.

The text is always a statement parameter, never inlined and never stored in a
column: PostgreSQL keeps the resulting tsvector, SQLite the FTS5 postings.
Callers own the extraction; this module only knows how to hand a document to
whichever backend is active.

Imported from the submodule, not from the package: `workspace.common.search`
owns the read path and stays free of a dependency on this one.
"""

from django.db import DEFAULT_DB_ALIAS, connections

from workspace.common.search import fts5_available
from workspace.common.uuids import parse_uuid_or_none


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
    if conn.vendor == "postgresql":  # pragma: no cover - exercised on PG only
        with conn.cursor() as cursor:
            cursor.execute(
                index.pg_update_sql(), [*document_values(index, values), param]
            )
        return
    if conn.vendor == "sqlite" and fts5_available():
        texts = document_values(index, values)
        with conn.cursor() as cursor:
            cursor.execute(index.sqlite_delete_sql(), [param])
            cursor.execute(index.sqlite_insert_sql(), [*texts, param])


def drop_document(index, pk, *, using=DEFAULT_DB_ALIAS):
    """Remove row *pk*'s document from the index.

    Call it while the row still exists: both backends resolve the pk against
    the base table, and SQLite's contentless table has no other way to find
    the posting (its rowid mirrors the base table's).
    """
    conn = connections[using]
    param = _adapt_pk(pk, conn)
    if conn.vendor == "postgresql":  # pragma: no cover - exercised on PG only
        with conn.cursor() as cursor:
            cursor.execute(index.pg_drop_sql(), [param])
        return
    if conn.vendor == "sqlite" and fts5_available():
        with conn.cursor() as cursor:
            cursor.execute(index.sqlite_delete_sql(), [param])


def _adapt_pk(pk, conn):
    """Bind a UUID the way the backend stores it (char(32) hex on SQLite)."""
    parsed = parse_uuid_or_none(pk)
    if parsed is None:
        return pk
    return parsed if conn.features.has_native_uuid_field else parsed.hex
