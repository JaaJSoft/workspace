import logging

from django.db import connection
from django.db.models import FloatField, Value

from .fallback import IcontainsFulltext
from .postgres import PostgresFulltext
from .schema import PG_TSV_COLUMN
from .sqlite import SqliteFtsFulltext

logger = logging.getLogger(__name__)

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
            # A transient failure (locked db, dropped connection) must not
            # pin the degraded fallback for the process lifetime; only a
            # real probe result is cached.
            logger.exception("FTS5 availability probe failed; will retry")
            return False
    return _fts5_available_cache


def apply_fulltext(qs, query, *, index):
    """Filter qs to rows matching query, annotating `search_rank`
    (float, higher = more relevant). The caller applies the final order_by.

    `index` is the model's FulltextIndex declaration; all schema names are
    derived from it.
    """
    if not query or not query.strip():
        return qs.none().annotate(search_rank=Value(0.0, output_field=FloatField()))
    return _active_backend().apply(
        qs,
        query,
        pg_column=PG_TSV_COLUMN,
        sqlite_fts_table=index.fts_table,
        fallback_fields=index.fallback_fields,
    )


def _active_backend():
    vendor = connection.vendor
    if vendor == "postgresql":
        return PostgresFulltext()  # pragma: no cover
    if vendor == "sqlite" and fts5_available():
        return SqliteFtsFulltext()
    return IcontainsFulltext()
