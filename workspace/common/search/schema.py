"""Declarative full-text index schema.

A FulltextIndex says which columns of a table are indexed with which
weight. Everything else is derived, never chosen: FTS table name
(<table>_fts), PG tsvector column (PG_TSV_COLUMN), GIN index name
(<table>_tsv_gin), trigger names (<fts_table>_ai/_ad/_au).

Migrations NEVER import these declarations. The SQL is generated once
(``manage.py fts_sql <dotted.path>``) and pasted as literal strings, so an
applied migration can never change meaning retroactively. Only the
post_migrate trigger-rebuild handler consumes the live declarations.
"""

from dataclasses import dataclass

from django.db import connections

PG_TSV_COLUMN = "search_tsv"

_FTS5_TOKENIZER = "unicode61 remove_diacritics 2"

# Same relevance ratios as PostgreSQL's default ts_rank weights
# {A: 1.0, B: 0.4, C: 0.2, D: 0.1}, expressed as bm25 multipliers.
_BM25_WEIGHTS = {"A": "10.0", "B": "4.0", "C": "2.0", "D": "1.0"}


@dataclass(frozen=True)
class Col:
    name: str
    weight: str = "A"
    # PG-side input cap in chars: a generated tsvector over ~1MB fails the
    # INSERT itself, so unbounded text columns must be truncated.
    cap: int | None = None

    def __post_init__(self):
        if self.weight not in _BM25_WEIGHTS:
            raise ValueError(f"weight must be one of A/B/C/D, got {self.weight!r}")


@dataclass(frozen=True)
class FulltextIndex:
    table: str
    columns: tuple

    def __post_init__(self):
        normalized = tuple(Col(c) if isinstance(c, str) else c for c in self.columns)
        if not normalized:
            raise ValueError("a FulltextIndex needs at least one column")
        object.__setattr__(self, "columns", normalized)

    @property
    def fts_table(self):
        return f"{self.table}_fts"

    @property
    def gin_index(self):
        return f"{self.table}_tsv_gin"

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

    def pg_reverse_sql(self):
        return (
            f"DROP INDEX IF EXISTS {self.gin_index};\n"
            f"ALTER TABLE {self.table} DROP COLUMN IF EXISTS {PG_TSV_COLUMN};"
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

    def sqlite_triggers_sql(self):
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
        weights = ", ".join(_BM25_WEIGHTS[c.weight] for c in self.columns)
        return (
            f"INSERT INTO {self.fts_table}({self.fts_table}) VALUES ('rebuild');\n"
            f"INSERT INTO {self.fts_table}({self.fts_table}, rank)\n"
            f"  VALUES ('rank', 'bm25({weights})');"
        )
