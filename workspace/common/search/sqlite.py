import re

from django.db.models import FloatField, Value
from django.db.models.expressions import RawSQL

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def to_fts5_match(query):
    """Turn arbitrary user text into a safe FTS5 MATCH expression.

    Each word is wrapped in double quotes so FTS5 treats it as a literal
    string (implicit AND between them). This neutralizes FTS5 operators
    ("-", "*", quotes, NEAR) that would otherwise raise 'fts5: syntax error'.
    """
    tokens = _WORD_RE.findall(query or "")
    return " ".join(f'"{t}"' for t in tokens)


class SqliteFtsFulltext:
    """Matches against an external-content FTS5 table kept in sync by triggers."""

    def apply(self, qs, query, *, sqlite_fts_table, **_):
        match = to_fts5_match(query)
        if not match:
            return qs.none().annotate(search_rank=Value(0.0, output_field=FloatField()))

        db_table = qs.model._meta.db_table
        # Correlated subquery so the FTS match runs INSIDE the caller's
        # queryset: its filters (access control included) and the match apply
        # in the same SQL, with no intermediate result cap that could drop a
        # caller's rows. sqlite_fts_table is a trusted constant supplied by
        # the caller, never user input. -rank flips FTS5's bm25 (lower =
        # better) into "higher = better"; the subquery yields NULL for
        # non-matching rows, filtered out below.
        return qs.annotate(
            search_rank=RawSQL(
                f'(SELECT -rank FROM "{sqlite_fts_table}" '
                f'WHERE "{sqlite_fts_table}".rowid = "{db_table}".rowid '
                f'AND "{sqlite_fts_table}" MATCH %s)',
                (match,),
                output_field=FloatField(),
            )
        ).filter(search_rank__isnull=False)
