from django.db.models import BooleanField, FloatField
from django.db.models.expressions import RawSQL


class PostgresFulltext:
    """Matches against the generated tsvector column through its GIN index."""

    def apply(self, qs, query, *, pg_column, **_):  # pragma: no cover
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
