import re

from django.db.models import BooleanField, FloatField, Value
from django.db.models.expressions import RawSQL

_WORD_CHAR = re.compile(r"[^\W_]")


class PostgresFulltext:
    """Matches against the generated tsvector column through its GIN index.

    The query goes through ``f_fts_text`` like the indexed text did, so both
    are cut into the same words, and every word must match: the semantics of
    the SQLite backend, which has no operators either.
    """

    def apply(self, qs, query, *, pg_column, **_):
        # Punctuation alone leaves no word to look for, and an empty tsquery
        # matches nothing anyway (with a NOTICE per statement).
        if not _WORD_CHAR.search(query or ""):
            return qs.none().annotate(search_rank=Value(0.0, output_field=FloatField()))
        # Qualify the tsvector column with the base table. When the queryset
        # joins another FTS-indexed table (which also has a search_tsv column,
        # e.g. tasks joined to projects for the archived filter), an
        # unqualified reference raises "column reference search_tsv is
        # ambiguous" on PostgreSQL. db_table is trusted model metadata.
        column = f'"{qs.model._meta.db_table}".{pg_column}'
        # Both RawSQL expressions live in the SELECT (annotations), compiled in
        # declaration order, so params bind deterministically as (query, query).
        # The filter references the annotation and adds no further param.
        return qs.annotate(
            search_rank=RawSQL(
                f"ts_rank({column}, plainto_tsquery('simple', f_fts_text(%s)))",
                (query,),
                output_field=FloatField(),
            ),
            _fts_match=RawSQL(
                f"({column} @@ plainto_tsquery('simple', f_fts_text(%s)))",
                (query,),
                output_field=BooleanField(),
            ),
        ).filter(_fts_match=True)
