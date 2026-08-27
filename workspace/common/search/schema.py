"""Declarative full-text index schema.

Two kinds of index live here, both presenting the same query surface to
``apply_fulltext`` (a ``search_tsv`` column on PostgreSQL, a rowid-keyed FTS5
table on SQLite):

``FulltextIndex``
    Derived by the database from columns of the table: a PostgreSQL
    ``GENERATED ALWAYS AS`` tsvector, an external-content FTS5 table kept in
    sync by triggers. Use it whenever the searchable text is a column.

``DerivedFulltextIndex``
    Fed by application code through ``index_document()``. The PostgreSQL
    column is a plain (writable) tsvector, the FTS5 table is contentless, so
    only lexemes are persisted and the source text never lands in a column.
    Use it when the text lives outside the database (a file blob).

Everything else is derived, never chosen: FTS table name (<table>_fts), PG
tsvector column (PG_TSV_COLUMN), GIN index name (<table>_tsv_gin), trigger
names (<fts_table>_ai/_ad/_au).

Migrations NEVER import these declarations. The SQL is generated once
(``manage.py fts_sql <dotted.path>``) and pasted as literal strings, so an
applied migration can never change meaning retroactively. Only the
post_migrate rebuild handler consumes the live declarations.
"""

from dataclasses import dataclass

from django.db import connections

PG_TSV_COLUMN = "search_tsv"

_FTS5_TOKENIZER = "unicode61 remove_diacritics 2"

# Same relevance ratios as PostgreSQL's default ts_rank weights
# {A: 1.0, B: 0.4, C: 0.2, D: 0.1}, expressed as bm25 multipliers.
_BM25_WEIGHTS = {"A": "10.0", "B": "4.0", "C": "2.0", "D": "1.0"}


@dataclass(frozen=True)
class _WeightedText:
    name: str
    weight: str = "A"
    # Input cap in chars: a tsvector over ~1MB is rejected by PostgreSQL, so
    # unbounded text must be truncated.
    cap: int | None = None

    def __post_init__(self):
        if self.weight not in _BM25_WEIGHTS:
            raise ValueError(f"weight must be one of A/B/C/D, got {self.weight!r}")


@dataclass(frozen=True)
class Col(_WeightedText):
    """A column of the indexed table. The cap is applied in SQL."""


@dataclass(frozen=True)
class Field(_WeightedText):
    """A key of a document pushed in by application code.

    Not necessarily a column: the cap is applied in Python, by
    ``index_document()``, before the value is bound.
    """


@dataclass(frozen=True)
class _BaseIndex:
    table: str

    @property
    def fts_table(self):
        return f"{self.table}_fts"

    @property
    def gin_index(self):
        return f"{self.table}_tsv_gin"

    def pg_reverse_sql(self):
        return (
            f"DROP INDEX IF EXISTS {self.gin_index};\n"
            f"ALTER TABLE {self.table} DROP COLUMN IF EXISTS {PG_TSV_COLUMN};"
        )

    def _rank_sql(self, entries):
        weights = ", ".join(_BM25_WEIGHTS[e.weight] for e in entries)
        return (
            f"INSERT INTO {self.fts_table}({self.fts_table}, rank)\n"
            f"  VALUES ('rank', 'bm25({weights})');"
        )


@dataclass(frozen=True)
class FulltextIndex(_BaseIndex):
    columns: tuple = ()

    def __post_init__(self):
        normalized = tuple(Col(c) if isinstance(c, str) else c for c in self.columns)
        if not normalized:
            raise ValueError("a FulltextIndex needs at least one column")
        object.__setattr__(self, "columns", normalized)

    @property
    def fallback_fields(self):
        return tuple(c.name for c in self.columns)

    def pg_forward_sql(self):
        parts = []
        for col in self.columns:
            expr = f"coalesce({col.name}, '')"
            if col.cap is not None:
                expr = f"left({expr}, {col.cap})"
            parts.append(
                f"setweight(to_tsvector('simple', f_unaccent({expr})), '{col.weight}')"
            )
        vector = " ||\n    ".join(parts)
        return (
            f"{self.pg_reverse_sql()}\n"
            f"\n"
            f"ALTER TABLE {self.table} ADD COLUMN {PG_TSV_COLUMN} tsvector\n"
            f"  GENERATED ALWAYS AS (\n"
            f"    {vector}\n"
            f"  ) STORED;\n"
            f"\n"
            f"CREATE INDEX {self.gin_index} ON {self.table} "
            f"USING gin ({PG_TSV_COLUMN});"
        )

    def sqlite_forward_sql(self):
        cols = ", ".join(self.fallback_fields)
        return (
            f"{self.sqlite_reverse_sql()}\n"
            f"\n"
            f"CREATE VIRTUAL TABLE {self.fts_table} USING fts5(\n"
            f"  {cols},\n"
            f"  content='{self.table}', content_rowid='rowid',\n"
            f"  tokenize='{_FTS5_TOKENIZER}'\n"
            f");\n"
            f"\n"
            f"{self._triggers(if_not_exists=False)}\n"
            f"\n"
            f"{self._reindex_sql()}"
        )

    def sqlite_reverse_sql(self):
        fts = self.fts_table
        return (
            f"DROP TRIGGER IF EXISTS {fts}_ai;\n"
            f"DROP TRIGGER IF EXISTS {fts}_ad;\n"
            f"DROP TRIGGER IF EXISTS {fts}_au;\n"
            f"DROP TABLE IF EXISTS {fts};"
        )

    def sqlite_post_migrate_sql(self):
        return f"{self._triggers(if_not_exists=True)}\n\n{self._reindex_sql()}"

    def _triggers(self, *, if_not_exists):
        fts = self.fts_table
        ine = "IF NOT EXISTS " if if_not_exists else ""
        cols = ", ".join(self.fallback_fields)
        new_vals = ", ".join(f"new.{c}" for c in self.fallback_fields)
        old_vals = ", ".join(f"old.{c}" for c in self.fallback_fields)
        return (
            f"CREATE TRIGGER {ine}{fts}_ai AFTER INSERT ON {self.table} BEGIN\n"
            f"  INSERT INTO {fts}(rowid, {cols})\n"
            f"  VALUES (new.rowid, {new_vals});\n"
            f"END;\n"
            f"\n"
            f"CREATE TRIGGER {ine}{fts}_ad AFTER DELETE ON {self.table} BEGIN\n"
            f"  INSERT INTO {fts}({fts}, rowid, {cols})\n"
            f"  VALUES ('delete', old.rowid, {old_vals});\n"
            f"END;\n"
            f"\n"
            f"CREATE TRIGGER {ine}{fts}_au AFTER UPDATE ON {self.table} BEGIN\n"
            f"  INSERT INTO {fts}({fts}, rowid, {cols})\n"
            f"  VALUES ('delete', old.rowid, {old_vals});\n"
            f"  INSERT INTO {fts}(rowid, {cols})\n"
            f"  VALUES (new.rowid, {new_vals});\n"
            f"END;"
        )

    def _reindex_sql(self):
        return (
            f"INSERT INTO {self.fts_table}({self.fts_table}) VALUES ('rebuild');\n"
            f"{self._rank_sql(self.columns)}"
        )


@dataclass(frozen=True)
class DerivedFulltextIndex(_BaseIndex):
    """An index whose document is built by application code.

    The text is bound as a statement parameter and never stored: PostgreSQL
    keeps a tsvector, SQLite a contentless FTS5 index. Nothing can rebuild
    either of them from the database alone, which is the point - and the
    reason a backfill command exists.

    SQLite caveat: the contentless table is keyed on the base table's implicit
    rowid. Django's SQLite schema editor remakes tables (create-copy-drop-
    rename) for several migration operations, which reassigns rowids and
    silently invalidates the mapping. Re-run the owning module's reindex
    command after such a migration.
    """

    fields: tuple = ()
    fallback_fields: tuple = ()
    pk_column: str = "uuid"

    def __post_init__(self):
        normalized = tuple(Field(f) if isinstance(f, str) else f for f in self.fields)
        if not normalized:
            raise ValueError("a DerivedFulltextIndex needs at least one field")
        object.__setattr__(self, "fields", normalized)
        names = {f.name for f in normalized}
        unknown = tuple(f for f in self.fallback_fields if f not in names)
        if unknown:
            raise ValueError(f"fallback_fields not declared as fields: {unknown}")

    @property
    def field_names(self):
        return tuple(f.name for f in self.fields)

    def pg_forward_sql(self):
        return (
            f"{self.pg_reverse_sql()}\n"
            f"\n"
            f"ALTER TABLE {self.table} ADD COLUMN {PG_TSV_COLUMN} tsvector;\n"
            f"\n"
            f"CREATE INDEX {self.gin_index} ON {self.table} "
            f"USING gin ({PG_TSV_COLUMN});"
        )

    def pg_update_sql(self):
        """UPDATE that writes one document. One bind per field, then the pk."""
        parts = [
            f"setweight(to_tsvector('simple', f_unaccent(%s)), '{f.weight}')"
            for f in self.fields
        ]
        vector = " ||\n    ".join(parts)
        return (
            f"UPDATE {self.table} SET {PG_TSV_COLUMN} =\n"
            f"    {vector}\n"
            f"WHERE {self.pk_column} = %s"
        )

    def pg_drop_sql(self):
        return f"UPDATE {self.table} SET {PG_TSV_COLUMN} = NULL WHERE {self.pk_column} = %s"

    def sqlite_forward_sql(self):
        cols = ", ".join(self.field_names)
        return (
            f"{self.sqlite_reverse_sql()}\n"
            f"\n"
            f"CREATE VIRTUAL TABLE {self.fts_table} USING fts5(\n"
            f"  {cols},\n"
            f"  content='', contentless_delete=1,\n"
            f"  tokenize='{_FTS5_TOKENIZER}'\n"
            f");\n"
            f"\n"
            f"{self._rank_sql(self.fields)}"
        )

    def sqlite_reverse_sql(self):
        return f"DROP TABLE IF EXISTS {self.fts_table};"

    def sqlite_post_migrate_sql(self):
        # A contentless table has no triggers to restore and nothing to
        # rebuild from; only the rank config is worth re-asserting.
        return self._rank_sql(self.fields)

    def sqlite_insert_sql(self):
        """INSERT that writes one document, resolving the rowid inline."""
        cols = ", ".join(self.field_names)
        placeholders = ", ".join("%s" for _ in self.fields)
        return (
            f"INSERT INTO {self.fts_table}(rowid, {cols})\n"
            f"  SELECT rowid, {placeholders} FROM {self.table} "
            f"WHERE {self.pk_column} = %s"
        )

    def sqlite_delete_sql(self):
        return (
            f"DELETE FROM {self.fts_table} WHERE rowid = "
            f"(SELECT rowid FROM {self.table} WHERE {self.pk_column} = %s)"
        )


_registered = {}


def register_fulltext_index(index):
    """Register a declaration for the post_migrate rebuild.

    Call from AppConfig.ready(). Keyed by table so a repeated ready() run
    replaces instead of duplicating.
    """
    _registered[index.table] = index


def registered_fulltext_indexes():
    return tuple(_registered.values())


def rebuild_sqlite_fts_indexes(sender, using, **kwargs):
    """Restore per-index SQLite state after a migration.

    Django's SQLite schema changes recreate tables (create-copy-drop-rename),
    which silently drops attached triggers. This restores them idempotently
    for every registered index whose FTS table exists. Contentless indexes
    have no triggers; they only get their rank config re-asserted.
    """
    conn = connections[using]
    if conn.vendor != "sqlite":
        return
    with conn.cursor() as cursor:
        for index in registered_fulltext_indexes():
            cursor.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=%s",
                [index.fts_table],
            )
            if cursor.fetchone() is None:
                continue
            cursor.executescript(index.sqlite_post_migrate_sql())
