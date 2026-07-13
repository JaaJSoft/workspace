import re

from django.db import connection
from django.db.models import BooleanField, FloatField, Q, Value
from django.db.models.expressions import RawSQL

_WORD_RE = re.compile(r"\w+", re.UNICODE)

_fts5_available_cache = None


def to_fts5_match(query):
    """Turn arbitrary user text into a safe FTS5 MATCH expression.

    Each word is wrapped in double quotes so FTS5 treats it as a literal
    string (implicit AND between them). This neutralizes FTS5 operators
    ("-", "*", quotes, NEAR) that would otherwise raise 'fts5: syntax error'.
    """
    tokens = _WORD_RE.findall(query or "")
    return " ".join(f'"{t}"' for t in tokens)


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
    vendor = connection.vendor
    if vendor == "postgresql":
        return _pg_filter(qs, query, pg_column)  # pragma: no cover
    if vendor == "sqlite" and fts5_available():
        return _sqlite_filter(qs, query, sqlite_fts_table)
    return _fallback_filter(qs, query, fallback_fields)


def _pg_filter(qs, query, pg_column):  # pragma: no cover
    # Both RawSQL expressions live in the SELECT (annotations), compiled in
    # declaration order, so params bind deterministically as (query, query).
    # The filter references the annotation and adds no further param.
    return qs.annotate(
        search_rank=RawSQL(
            f"ts_rank({pg_column}, websearch_to_tsquery('simple', f_unaccent(%s)))",
            (query,),
            output_field=FloatField(),
        ),
        _fts_match=RawSQL(
            f"({pg_column} @@ websearch_to_tsquery('simple', f_unaccent(%s)))",
            (query,),
            output_field=BooleanField(),
        ),
    ).filter(_fts_match=True)


def _sqlite_filter(qs, query, fts_table):
    match = to_fts5_match(query)
    if not match:
        return qs.none().annotate(search_rank=Value(0.0, output_field=FloatField()))

    db_table = qs.model._meta.db_table
    # Correlated subquery so the FTS match runs INSIDE the caller's queryset:
    # its filters (access control included) and the match apply in the same
    # SQL, with no intermediate result cap that could drop a caller's rows.
    # fts_table is a trusted constant supplied by the caller, never user input.
    # -rank flips FTS5's bm25 (lower = better) into "higher = better";
    # the subquery yields NULL for non-matching rows, filtered out below.
    return qs.annotate(
        search_rank=RawSQL(
            f'(SELECT -rank FROM "{fts_table}" '
            f'WHERE "{fts_table}".rowid = "{db_table}".rowid '
            f'AND "{fts_table}" MATCH %s)',
            (match,),
            output_field=FloatField(),
        )
    ).filter(search_rank__isnull=False)


def _fallback_filter(qs, query, fields):
    condition = Q()
    for field in fields:
        condition |= Q(**{f"{field}__icontains": query})
    return qs.filter(condition).annotate(
        search_rank=Value(0.0, output_field=FloatField())
    )
