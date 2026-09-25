"""Declarative vector index schema.

A ``VectorIndex`` answers "the k rows whose vector is nearest to this one,
within one partition" (typically one user's rows). The source of truth is a
bytes column of the row holding the unit-length vector as little-endian
float32. Everything else is derived from it and can be rebuilt at any time
(``manage.py rebuild_vector_index``):

- PostgreSQL with pgvector: a ``vector(dims)`` column next to the source,
  under an HNSW index.
- SQLite with sqlite-vec: a ``vec0`` virtual table with the partition as a
  partition key, keyed on the row's UUID. Never on the implicit rowid: Django
  rebuilds a SQLite table for many operations (any AddField with a default)
  and SQLite reassigns implicit rowids on the copy, so an index keyed on them
  keeps answering - with the wrong rows.
- Anything else: nothing - the fallback reads the source column directly.

The source column is an ordinary model field (a ``BinaryField``) added by the
owning app's migrations like any other; only the derived structures are
created from the SQL here.

Derived names, never chosen: vec0 table ``<table>_<source_column>_vec``, PG
column ``<source_column>_vec``, HNSW index ``<table>_<source_column>_hnsw``.

Migrations NEVER import these declarations. The SQL is generated once
(``manage.py vector_sql <dotted.path>``) and pasted as literal strings into a
``RunVectorIndexSQL`` operation, so an applied migration can never change
meaning retroactively. ``dims`` is frozen there too: switching to an embedding
model of another size is a schema change plus a reindex.
"""

import re
from dataclasses import dataclass

# pgvector cannot build an HNSW index over a `vector` wider than this.
MAX_DIMS = 2000
# sqlite-vec refuses a KNN query asking for more; the limit applies to every
# backend so a caller cannot write code that only works on one of them.
MAX_K = 4096

# Name of the vector column inside the vec0 table. Never visible outside it.
SQLITE_VECTOR_COLUMN = "embedding"

# metric -> (sqlite-vec distance_metric, pgvector operator, pgvector opclass)
_METRICS = {
    "cosine": ("cosine", "<=>", "vector_cosine_ops"),
    "l2": ("l2", "<->", "vector_l2_ops"),
}
_PARTITION_TYPES = ("integer", "text")

# Every name below is interpolated into SQL, so every name is checked.
_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


@dataclass(frozen=True, kw_only=True)
class VectorIndex:
    table: str
    dims: int
    # BinaryField holding the vector: `dims` little-endian float32, unit length.
    source_column: str
    # Column every query is scoped to (an owner). None for a global index.
    partition_column: str | None = None
    # How SQLite stores the partition column: "integer" for an integer
    # foreign key, "text" for a UUID one (char(32) hex on SQLite).
    partition_type: str = "integer"
    # A UUID primary key (char(32) hex on SQLite): the vec0 table is keyed on it.
    pk_column: str = "uuid"
    metric: str = "cosine"
    # Vectors per vec0 chunk. Every partition value gets its own chunk,
    # zero-filled to this size and never reclaimed, so sqlite-vec's default of
    # 1024 costs 2 MB per user at 512 dimensions however few vectors they own.
    # 64 keeps a small partition small for ~13% slower KNN on a large one.
    chunk_size: int = 64

    def __post_init__(self):
        names = (
            self.table,
            self.source_column,
            self.pk_column,
            *((self.partition_column,) if self.partition_column else ()),
        )
        for name in names:
            if not isinstance(name, str) or not _IDENTIFIER_RE.match(name):
                raise ValueError(f"invalid SQL identifier: {name!r}")
        if isinstance(self.dims, bool) or not isinstance(self.dims, int):
            raise ValueError(f"dims must be an integer, got {self.dims!r}")
        if not 1 <= self.dims <= MAX_DIMS:
            raise ValueError(f"dims must be between 1 and {MAX_DIMS}, got {self.dims}")
        if self.metric not in _METRICS:
            raise ValueError(
                f"metric must be one of {sorted(_METRICS)}, got {self.metric!r}"
            )
        chunk_size = self.chunk_size
        if (
            isinstance(chunk_size, bool)
            or not isinstance(chunk_size, int)
            or not 8 <= chunk_size <= 4096
            or chunk_size % 8
        ):
            raise ValueError(
                f"chunk_size must be a multiple of 8 up to 4096, got {chunk_size!r}"
            )
        if self.partition_type not in _PARTITION_TYPES:
            raise ValueError(
                f"partition_type must be one of {_PARTITION_TYPES}, "
                f"got {self.partition_type!r}"
            )

    # -- derived names ---------------------------------------------------

    @property
    def key(self):
        return (self.table, self.source_column)

    @property
    def vec_table(self):
        return f"{self.table}_{self.source_column}_vec"

    @property
    def pg_column(self):
        return f"{self.source_column}_vec"

    @property
    def hnsw_index(self):
        return f"{self.table}_{self.source_column}_hnsw"

    @property
    def partitioned(self):
        return self.partition_column is not None

    @property
    def pg_operator(self):
        return _METRICS[self.metric][1]

    # -- migration SQL ---------------------------------------------------

    def pg_forward_sql(self):
        """Add the vector column and its HNSW index, when pgvector allows it.

        Conditional on purpose: a stock PostgreSQL image has no pgvector, and
        the migration must still apply there - the fallback serves queries
        from the source column. The same goes for a role that may not create
        the extension. The source column is untouched either way.
        """
        opclass = _METRICS[self.metric][2]
        return (
            f"{self.pg_reverse_sql()}\n"
            f"\n"
            f"DO $$\n"
            f"BEGIN\n"
            f"  IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') THEN\n"
            f"    CREATE EXTENSION IF NOT EXISTS vector;\n"
            f"    ALTER TABLE {self.table} ADD COLUMN {self.pg_column} vector({self.dims});\n"
            f"    CREATE INDEX {self.hnsw_index} ON {self.table}\n"
            f"      USING hnsw ({self.pg_column} {opclass});\n"
            f"  END IF;\n"
            f"EXCEPTION WHEN insufficient_privilege THEN\n"
            f"  RAISE NOTICE 'pgvector is installed but may not be enabled by this role; "
            f"{self.table}.{self.source_column} is searched without an index';\n"
            f"END\n"
            f"$$;"
        )

    def pg_reverse_sql(self):
        # The extension stays: other indexes, or other applications, may use it.
        return (
            f"DROP INDEX IF EXISTS {self.hnsw_index};\n"
            f"ALTER TABLE {self.table} DROP COLUMN IF EXISTS {self.pg_column};"
        )

    def sqlite_forward_sql(self):
        """Create the vec0 table and fill it from every row carrying a vector.

        Only runs where sqlite-vec is loaded (RunVectorIndexSQL checks).
        """
        metric = _METRICS[self.metric][0]
        partition = (
            f"  {self.partition_column} {self.partition_type} partition key,\n"
            if self.partitioned
            else ""
        )
        return (
            f"{self.sqlite_reverse_sql()}\n"
            f"\n"
            f"CREATE VIRTUAL TABLE {self.vec_table} USING vec0(\n"
            f"  {self.pk_column} text primary key,\n"
            f"{partition}"
            f"  {SQLITE_VECTOR_COLUMN} float[{self.dims}] distance_metric={metric},\n"
            f"  chunk_size={self.chunk_size}\n"
            f");\n"
            f"\n"
            f"{self.sqlite_backfill_sql()};"
        )

    def sqlite_reverse_sql(self):
        return f"DROP TABLE IF EXISTS {self.vec_table};"

    # -- runtime SQL -----------------------------------------------------

    def _partition_select(self):
        return f"{self.partition_column}, " if self.partitioned else ""

    @property
    def byte_length(self):
        return self.dims * 4

    def _sqlite_valid_source(self, prefix=""):
        """SQL true for a source vec0 accepts.

        vec0 refuses any other blob, and one refusal aborts the whole INSERT
        ... SELECT: a truncated blob, or rows still at the size of a previous
        embedding model, would otherwise fail the migration and every rebuild.
        """
        return f"length({prefix}{self.source_column}) = {self.byte_length}"

    def _indexable_where(self):
        """Rows the vec0 table can hold: a valid vector and, if any, a partition."""
        where = self._sqlite_valid_source()
        if self.partitioned:
            where += f" AND {self.partition_column} IS NOT NULL"
        return where

    def sqlite_backfill_sql(self):
        """Copy every indexable row's vector into the vec0 table."""
        return (
            f"INSERT INTO {self.vec_table}"
            f"({self.pk_column}, {self._partition_select()}{SQLITE_VECTOR_COLUMN})\n"
            f"  SELECT {self.pk_column}, {self._partition_select()}{self.source_column} "
            f"FROM {self.table}\n"
            f"  WHERE {self._indexable_where()}"
        )

    def sqlite_insert_sql(self):
        """Copy one row's vector into the vec0 table, straight from the row.

        sqlite-vec reads the same float32 blob the source column stores, so
        the entry is derived by the statement itself: it cannot disagree with
        the row, and the partition is the row's own.
        """
        return f"{self.sqlite_backfill_sql()} AND {self.pk_column} = %s"

    def sqlite_delete_sql(self):
        """Binds the pk. Needs nothing from the row, so it works after a delete."""
        return f"DELETE FROM {self.vec_table} WHERE {self.pk_column} = %s"

    def sqlite_nearest_sql(self):
        """KNN on the vec0 table, each entry flagged live or not.

        Binds the query blob, k, then the partition. The KNN is the CTE, as
        sqlite-vec requires: its MATCH/k constraints must reach the virtual
        table untouched by the join. An entry is live while its row exists and
        still holds a vector the fallback would read; the others come back
        flagged rather than filtered, so the caller can tell a short answer
        from one that dead entries crowded out.
        """
        partition = f" AND {self.partition_column} = %s" if self.partitioned else ""
        return (
            f"WITH knn AS (\n"
            f"  SELECT {self.pk_column}, distance FROM {self.vec_table}\n"
            f"  WHERE {SQLITE_VECTOR_COLUMN} MATCH %s AND k = %s{partition}\n"
            f")\n"
            f"SELECT knn.{self.pk_column}, knn.distance,\n"
            f"  t.{self.pk_column} IS NOT NULL AND {self._sqlite_valid_source('t.')}\n"
            f"FROM knn\n"
            f"LEFT JOIN {self.table} AS t ON t.{self.pk_column} = knn.{self.pk_column}\n"
            f"ORDER BY knn.distance"
        )

    def pg_update_sql(self):
        """Write the source and its vector. Binds bytes, vector literal, pk."""
        return (
            f"UPDATE {self.table} SET {self.source_column} = %s, "
            f"{self.pg_column} = %s::vector WHERE {self.pk_column} = %s"
        )

    def pg_set_vector_sql(self):
        """Derive the vector column from the source. Binds the literal, the pk."""
        return f"UPDATE {self.table} SET {self.pg_column} = %s::vector WHERE {self.pk_column} = %s"

    def pg_clear_sql(self):
        return (
            f"UPDATE {self.table} SET {self.source_column} = NULL, "
            f"{self.pg_column} = NULL WHERE {self.pk_column} = %s"
        )

    def pg_nearest_sql(self):
        """Binds the vector literal, the partition, then k."""
        partition = f" AND {self.partition_column} = %s" if self.partitioned else ""
        return (
            f"SELECT {self.pk_column}, {self.pg_column} {self.pg_operator} %s::vector "
            f"AS distance\n"
            f"FROM {self.table}\n"
            f"WHERE {self.pg_column} IS NOT NULL{partition}\n"
            f"ORDER BY distance\n"
            f"LIMIT %s"
        )

    def update_source_sql(self):
        """Binds bytes (or None), then the pk."""
        return f"UPDATE {self.table} SET {self.source_column} = %s WHERE {self.pk_column} = %s"

    def source_rows_sql(self):
        """Every (pk, vector bytes) the fallback scans. Binds the partition."""
        partition = f" AND {self.partition_column} = %s" if self.partitioned else ""
        return (
            f"SELECT {self.pk_column}, {self.source_column} FROM {self.table} "
            f"WHERE {self.source_column} IS NOT NULL{partition}"
        )

    def pg_source_rows_page_sql(self):
        """One keyset page of (pk, vector bytes). Binds after, after, limit.

        `after` is the last pk of the previous page, None for the first one.
        """
        return (
            f"SELECT {self.pk_column}, {self.source_column} FROM {self.table}\n"
            f"WHERE {self.source_column} IS NOT NULL "
            f"AND (%s::uuid IS NULL OR {self.pk_column} > %s)\n"
            f"ORDER BY {self.pk_column}\n"
            f"LIMIT %s"
        )


_registered = {}


def register_vector_index(index):
    """Make a declaration known to the checks and to rebuild_vector_index.

    Call from AppConfig.ready(). Keyed by (table, source column) so a repeated
    ready() run replaces instead of duplicating.
    """
    _registered[index.key] = index


def registered_vector_indexes():
    return tuple(_registered.values())
