"""Write side of a DerivedFulltextIndex: push one document into the index.

The text is always a statement parameter, never inlined and never stored in a
column: PostgreSQL keeps the resulting tsvector, SQLite the FTS5 postings.
Callers own the extraction; this module only knows how to hand a document to
whichever backend is active.

Imported from the submodule, not from the package: `workspace.common.search`
owns the read path and stays free of a dependency on this one.
"""

from django.db import DEFAULT_DB_ALIAS, connections, transaction

from workspace.common.rowids import adapt_pk, claim_rowid
from workspace.common.search import fts5_available


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
    param = adapt_pk(pk, conn)
    texts = document_values(index, values)
    if conn.vendor == "postgresql":  # pragma: no cover - exercised on PG only
        with conn.cursor() as cursor:
            cursor.execute(index.pg_update_sql(), [*texts, param])
        return
    if conn.vendor == "sqlite" and fts5_available():
        # One transaction for the whole document. Left in autocommit these
        # would be three separate write transactions per row, so a backfill
        # would take the write lock three times per file - and a failure
        # between the delete and the insert would drop the document from the
        # index without anything noticing.
        with transaction.atomic(using=using), conn.cursor() as cursor:
            if index.rowid_column and not claim_rowid(
                cursor,
                read_sql=index.sqlite_read_rowid_sql(),
                assign_sql=index.sqlite_assign_rowid_sql(),
                pk=pk,
                param=param,
            ):
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
            cursor.execute(index.sqlite_delete_sql(), [adapt_pk(pk, conn)])
