from django.db.models import FloatField, Q, Value


class IcontainsFulltext:
    """Unindexed icontains scan, so search still works without FTS support."""

    def apply(self, qs, query, *, fallback_fields, **_):
        condition = Q()
        for field in fallback_fields:
            condition |= Q(**{f"{field}__icontains": query})
        return qs.filter(condition).annotate(
            search_rank=Value(0.0, output_field=FloatField())
        )
