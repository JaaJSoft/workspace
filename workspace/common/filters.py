from django.db.models.functions import Lower
from rest_framework.filters import OrderingFilter


class CaseInsensitiveOrderingFilter(OrderingFilter):
    """OrderingFilter that wraps ``name`` in ``Lower()`` for case-insensitive sort."""

    def get_ordering(self, request, queryset, view):
        ordering = super().get_ordering(request, queryset, view)
        if not ordering:
            return ordering
        result = []
        for field in ordering:
            if field == 'name':
                result.append(Lower('name'))
            elif field == '-name':
                result.append(Lower('name').desc())
            else:
                result.append(field)
        return result
