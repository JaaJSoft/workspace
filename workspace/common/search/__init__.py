from django.db import connection
from django.db.models import FloatField, Value

from .fallback import IcontainsFulltext
from .postgres import PostgresFulltext
from .sqlite import SqliteFtsFulltext

_fts5_available_cache = None


def fts5_available():
    """True when the active SQLite build was compiled with FTS5."""
    global _fts5_available_cache
    if _fts5_available_cache is None:
        try:
            with connection.cursor() as c:
                c.execute(
                    "SELECT 1 FROM pragma_compile_options "
                    "WHERE compile_options = 'ENABLE_FTS5'"
                )
                _fts5_available_cache = c.fetchone() is not None
        except Exception:
            _fts5_available_cache = False
    return _fts5_available_cache


def apply_fulltext(qs, query, *, pg_column, sqlite_fts_table, fallback_fields):
    """Filter qs to rows matching query, annotating `search_rank`
    (float, higher = more relevant). The caller applies the final order_by.
    """
    if not query or not query.strip():
        return qs.none().annotate(search_rank=Value(0.0, output_field=FloatField()))
    return _active_backend().apply(
        qs,
        query,
        pg_column=pg_column,
        sqlite_fts_table=sqlite_fts_table,
        fallback_fields=fallback_fields,
    )


def _active_backend():
    vendor = connection.vendor
    if vendor == "postgresql":
        return PostgresFulltext()  # pragma: no cover
    if vendor == "sqlite" and fts5_available():
        return SqliteFtsFulltext()
    return IcontainsFulltext()
