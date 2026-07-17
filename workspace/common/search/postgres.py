from django.db.models import BooleanField, FloatField
from django.db.models.expressions import RawSQL


class PostgresFulltext:
    """Matches against the generated tsvector column through its GIN index."""

    def apply(self, qs, query, *, pg_column, **_):
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
                f"ts_rank({column}, websearch_to_tsquery('simple', f_unaccent(%s)))",
                (query,),
                output_field=FloatField(),
            ),
            _fts_match=RawSQL(
                f"({column} @@ websearch_to_tsquery('simple', f_unaccent(%s)))",
                (query,),
                output_field=BooleanField(),
            ),
        ).filter(_fts_match=True)
