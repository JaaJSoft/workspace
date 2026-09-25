"""pgvector: probing for it and KNN over the HNSW index."""

from django.db import transaction

from .encoding import pg_literal

# Iterative index scans (pgvector 0.8). Without them an HNSW scan stops after
# hnsw.ef_search candidates (40 by default) and the partition filter is applied
# to those alone, so a user owning a small share of the table gets fewer than
# k results - often none. Older versions therefore skip the HNSW index on a
# partitioned query and scan the partition exactly.
ITERATIVE_SCAN_VERSION = (0, 8)


def pgvector_version(conn):
    """The installed pgvector version as a tuple, or None."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        row = cursor.fetchone()
    if row is None:
        return None
    return tuple(int(part) for part in row[0].split(".") if part.isdigit())


def pgvector_available(conn):
    """Whether the server can install pgvector at all."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
        return cursor.fetchone() is not None


def vector_column_exists(conn, index):
    """Whether the migration (or a rebuild) created the index's vector column.

    It may not have: the migration skips it where pgvector was not available.
    """
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM pg_attribute "
            "WHERE attrelid = to_regclass(%s) AND attname = %s AND NOT attisdropped",
            [index.table, index.pg_column],
        )
        return cursor.fetchone() is not None


class PgvectorNearest:
    name = "pgvector"

    def __init__(self, iterative_scan):
        self.iterative_scan = iterative_scan

    def nearest(self, index, conn, query, *, partition, k):
        params = [pg_literal(query)]
        if index.partitioned:
            params.append(partition)
        params.append(k)
        # SET LOCAL needs a transaction and ends with it, so the setting never
        # leaks to the next user of a pooled connection.
        with transaction.atomic(using=conn.alias), conn.cursor() as cursor:
            if index.partitioned and self.iterative_scan:
                cursor.execute("SET LOCAL hnsw.iterative_scan = strict_order")
            elif index.partitioned:
                cursor.execute("SET LOCAL enable_indexscan = off")
            cursor.execute(index.pg_nearest_sql(), params)
            return cursor.fetchall()
