from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response


class OptInLimitOffsetPagination(LimitOffsetPagination):
    """Limit/offset slicing that only engages when the client asks for it.

    Without a ``limit`` query parameter the endpoint behaves exactly as if it
    were unpaginated and returns the full array. With one, the body is still a
    bare array (no ``{count, next, results}`` envelope), and the
    ``X-Has-More`` response header says whether another page exists past
    ``offset + limit``. Existence of a next page is detected by fetching one
    extra row, so no COUNT query is issued.
    """

    default_limit = None
    max_limit = 1000
    template = None

    def paginate_queryset(self, queryset, request, view=None):
        self.request = request
        self.limit = self.get_limit(request)
        if self.limit is None:
            return None
        self.offset = self.get_offset(request)
        rows = list(queryset[self.offset : self.offset + self.limit + 1])
        self.has_more = len(rows) > self.limit
        return rows[: self.limit]

    def get_paginated_response(self, data):
        return Response(
            data, headers={"X-Has-More": "true" if self.has_more else "false"}
        )

    def get_paginated_response_schema(self, schema):
        return schema

    def get_results(self, data):
        return data
