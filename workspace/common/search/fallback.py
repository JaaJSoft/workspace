from django.db.models import FloatField, Q, Value


class IcontainsFulltext:
    """Unindexed substring scan, so search still works without FTS support.

    Folds case and accents like the full-text indexes it stands in for.
    """

    def apply(self, qs, query, *, fallback_fields, **_):
        # An empty Q() matches every row; no fields must mean no matches.
        if not fallback_fields:
            return qs.none().annotate(search_rank=Value(0.0, output_field=FloatField()))
        condition = Q()
        for field in fallback_fields:
            condition |= Q(**{f"{field}__ficontains": query})
        return qs.filter(condition).annotate(
            search_rank=Value(0.0, output_field=FloatField())
        )
