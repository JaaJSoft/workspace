from django.db.models.functions import Lower
from rest_framework.filters import OrderingFilter


class CaseInsensitiveOrderingFilter(OrderingFilter):
    """OrderingFilter that wraps text fields in ``Lower()`` for case-insensitive sort.

    Set ``ordering_case_insensitive_fields`` on the view to list which fields
    should be lowered. Defaults to all ``ordering_fields`` when not set.
    """

    def get_ordering(self, request, queryset, view):
        ordering = super().get_ordering(request, queryset, view)
        if not ordering:
            return ordering
        ci_fields = set(
            getattr(view, "ordering_case_insensitive_fields", None)
            or getattr(view, "ordering_fields", [])
        )
        result = []
        for field in ordering:
            if not isinstance(field, str):
                result.append(field)
                continue
            bare = field.lstrip("-")
            if bare in ci_fields:
                expr = Lower(bare)
                result.append(expr.desc() if field.startswith("-") else expr)
            else:
                result.append(field)
        return result
