"""pgvector: probing for it and KNN over the HNSW index."""

from django.db import transaction

from .encoding import pg_literal

# Iterative index scans (pgvector 0.8): an HNSW scan keeps going past
# hnsw.ef_search candidates until it has k rows.
ITERATIVE_SCAN_VERSION = (0, 8)
# Before them, an HNSW scan returns at most hnsw.ef_search rows: 40 by default,
# raised to k per query up to pgvector's own ceiling, exact scan beyond it.
DEFAULT_EF_SEARCH = 40
MAX_EF_SEARCH = 1000


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

    def __init__(self, version):
        self.version = version

    def nearest(self, index, conn, query, *, partition, k):
        params = [pg_literal(query)]
        if index.partitioned:
            params.append(partition)
        params.append(k)
        setting = self._scan_setting(index, k)
        if setting is None:
            exact = index.partitioned or k > MAX_EF_SEARCH
            with conn.cursor() as cursor:
                cursor.execute(index.pg_nearest_sql(exact=exact), params)
                return cursor.fetchall()
        with transaction.atomic(using=conn.alias), conn.cursor() as cursor:
            cursor.execute(setting)
            cursor.execute(index.pg_nearest_sql(exact=False), params)
            rows = cursor.fetchall()
            # SET LOCAL lasts until the end of the transaction, and inside a
            # caller's atomic() this block is only a savepoint, whose release
            # keeps it. Rolling back is what scopes it to this query - on a
            # read that discards nothing else.
            transaction.set_rollback(True, using=conn.alias)
        return rows

    def _scan_setting(self, index, k):
        """The SET LOCAL an HNSW scan needs to return k rows, if any.

        None when no setting is needed, or when no HNSW scan can be trusted
        to return k rows and the query must be exact: always for a
        partitioned index (see pg_nearest_sql), and past MAX_EF_SEARCH
        without iterative scans.
        """
        if index.partitioned:
            return None
        if self.version >= ITERATIVE_SCAN_VERSION:
            return "SET LOCAL hnsw.iterative_scan = strict_order"
        if DEFAULT_EF_SEARCH < k <= MAX_EF_SEARCH:
            return f"SET LOCAL hnsw.ef_search = {int(k)}"
        return None
