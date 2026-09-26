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
# partition_type -> the vec0 partition key's column type. A UUID is stored as
# char(32) hex on SQLite.
_PARTITION_TYPES = {"integer": "integer", "uuid": "text"}

# Every name below is interpolated into SQL unquoted, so every name is checked,
# and PostgreSQL's reserved words are refused: unquoted, `user` is CURRENT_USER.
_IDENTIFIER_RE = re.compile(r"[a-z_][a-z0-9_]*")
_MAX_IDENTIFIER_BYTES = 63
_RESERVED = frozenset(
    """all analyse analyze and any array as asc asymmetric both case cast check
    collate column constraint create current_catalog current_date current_role
    current_time current_timestamp current_user default deferrable desc
    distinct do else end except false fetch for foreign from grant group
    having in initially intersect into lateral leading limit localtime
    localtimestamp not null offset on only or order placing primary
    references returning select session_user some symmetric system_user table
    then to trailing true union unique user using variadic when where window
    with""".split()
)


@dataclass(frozen=True, kw_only=True)
class VectorIndex:
    table: str
    dims: int
    # BinaryField holding the vector: `dims` little-endian float32, unit length.
    source_column: str
    # Column every query is scoped to (an owner). None for a global index.
    partition_column: str | None = None
    # "integer" for an integer foreign key, "uuid" for a UUID one. The
    # partition a query passes is converted to it, on every backend alike.
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
            *((self.partition_column,) if self.partitioned else ()),
        )
        for name in names:
            if (
                not isinstance(name, str)
                or not _IDENTIFIER_RE.fullmatch(name)
                or name in _RESERVED
            ):
                raise ValueError(f"invalid SQL identifier: {name!r}")
        # PostgreSQL cuts identifiers at 63 bytes without a word, so two
        # declarations whose names share their first 63 bytes would share one
        # vector column or one HNSW index. Names are ASCII, so bytes = chars.
        for name in (*names, self.pg_column, self.hnsw_index):
            if len(name) > _MAX_IDENTIFIER_BYTES:
                raise ValueError(
                    f"{name!r} is longer than PostgreSQL's "
                    f"{_MAX_IDENTIFIER_BYTES}-byte identifier limit"
                )
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
                f"partition_type must be one of {tuple(_PARTITION_TYPES)}, "
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
        """Ready the vector column, without destroying one already filled.

        Conditional on purpose: a stock PostgreSQL image has no pgvector, and
        the migration must still apply there - the fallback serves queries
        from the source column. The same goes for a role that may not create
        the extension. The source column is untouched either way.

        Re-runnable: a later migration, a rollback then re-apply, or a rebuild
        replays it on a populated table. A column already of the right size
        keeps its vectors; only one of another size is dropped. The HNSW index
        is dropped here and recreated by pg_index_sql once the column is
        filled - its operator class carries the metric, and building it over a
        filled column is several times faster than maintaining it row by row.
        """
        return (
            f"DO $$\n"
            f"BEGIN\n"
            f"  IF NOT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') THEN\n"
            f"    RETURN;\n"
            f"  END IF;\n"
            f"  CREATE EXTENSION IF NOT EXISTS vector;\n"
            f"  DROP INDEX IF EXISTS {self.hnsw_index};\n"
            f"  IF EXISTS (\n"
            f"    SELECT 1 FROM pg_attribute\n"
            f"    WHERE attrelid = to_regclass('{self.table}') AND attname = '{self.pg_column}'\n"
            f"      AND NOT attisdropped\n"
            f"      AND (atttypid <> 'vector'::regtype OR atttypmod <> {self.dims})\n"
            f"  ) THEN\n"
            f"    ALTER TABLE {self.table} DROP COLUMN {self.pg_column};\n"
            f"  END IF;\n"
            f"  ALTER TABLE {self.table} ADD COLUMN IF NOT EXISTS {self.pg_column} vector({self.dims});\n"
            f"EXCEPTION WHEN insufficient_privilege THEN\n"
            f"  RAISE NOTICE 'pgvector is installed but may not be enabled by this role; "
            f"{self.table}.{self.source_column} is searched without an index';\n"
            f"END\n"
            f"$$;"
        )

    def pg_index_sql(self):
        """Build the HNSW index, once the column is filled.

        A pgvector older than 0.5 has no hnsw access method: the column then
        serves exact scans, which are correct, only slower.
        """
        opclass = _METRICS[self.metric][2]
        return (
            f"DO $$\n"
            f"BEGIN\n"
            f"  IF to_regtype('vector') IS NULL OR NOT EXISTS (\n"
            f"    SELECT 1 FROM pg_attribute\n"
            f"    WHERE attrelid = to_regclass('{self.table}') AND attname = '{self.pg_column}'\n"
            f"      AND NOT attisdropped\n"
            f"  ) THEN\n"
            f"    RETURN;\n"
            f"  END IF;\n"
            f"  CREATE INDEX IF NOT EXISTS {self.hnsw_index} ON {self.table}\n"
            f"    USING hnsw ({self.pg_column} {opclass});\n"
            f"EXCEPTION WHEN undefined_object THEN\n"
            f"  RAISE NOTICE 'this pgvector has no hnsw index; "
            f"{self.table}.{self.pg_column} is searched exactly';\n"
            f"END\n"
            f"$$;"
        )

    @property
    def pg_backfill(self):
        """What RunVectorIndexSQL needs to fill the column, as literals."""
        return {
            "table": self.table,
            "pk_column": self.pk_column,
            "source_column": self.source_column,
            "column": self.pg_column,
            "dims": self.dims,
        }

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
            f"  {self.partition_column} {_PARTITION_TYPES[self.partition_type]} "
            f"partition key,\n"
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

    def _partition_where(self):
        return f" AND {self.partition_column} = %s" if self.partitioned else ""

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
        partition = self._partition_where()
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

    def pg_set_vector_sql(self):
        """Write the vector column. Binds the literal (or None), then the pk."""
        return f"UPDATE {self.table} SET {self.pg_column} = %s::vector WHERE {self.pk_column} = %s"

    def pg_nearest_sql(self, *, exact):
        """Binds the vector literal, the partition, then k.

        *exact* computes every distance inside a MATERIALIZED CTE, which the
        planner cannot turn into an HNSW scan: it reads the rows the WHERE
        clause leaves (a partition, through the owner's btree) and sorts them.
        An HNSW scan filters only the candidates it visits - hnsw.ef_search of
        them, or hnsw.max_scan_tuples with iterative scans - so a small
        partition among many nearer rows of other owners comes back short, or
        empty.
        """
        partition = self._partition_where()
        candidates = (
            f"SELECT {self.pk_column}, {self.pg_column} {self.pg_operator} %s::vector "
            f"AS distance\n"
            f"FROM {self.table}\n"
            f"WHERE {self.pg_column} IS NOT NULL"
            f" AND octet_length({self.source_column}) = {self.byte_length}{partition}\n"
        )
        if not exact:
            return f"{candidates}ORDER BY distance\nLIMIT %s"
        return (
            f"WITH candidates AS MATERIALIZED (\n{candidates})\n"
            f"SELECT {self.pk_column}, distance FROM candidates\n"
            f"ORDER BY distance\n"
            f"LIMIT %s"
        )

    def update_source_sql(self):
        """Binds bytes (or None), then the pk."""
        return f"UPDATE {self.table} SET {self.source_column} = %s WHERE {self.pk_column} = %s"

    def source_rows_sql(self):
        """Every (pk, vector bytes) the fallback scans. Binds the partition."""
        partition = self._partition_where()
        return (
            f"SELECT {self.pk_column}, {self.source_column} FROM {self.table} "
            f"WHERE {self.source_column} IS NOT NULL{partition}"
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
