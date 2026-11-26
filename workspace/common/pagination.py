"""The paginators lists of the API answer through.

A collection answers a bare array: where a page stops is said by headers, and
an empty array is always the end. ``ListPagination`` is the paginator of the
API standard. ``OptInLimitOffsetPagination`` is the form lists had before it,
for the ones that have not moved over.
"""

from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import BasePagination, LimitOffsetPagination
from rest_framework.response import Response

HAS_MORE_HEADER = "X-Has-More"
TOTAL_COUNT_HEADER = "X-Total-Count"


def _query_value(request, name, field, default):
    """The *name* query parameter read through *field*; a 400 naming it otherwise."""
    raw = request.query_params.get(name)
    if raw is None or raw == "":
        return default
    try:
        return field.run_validation(raw)
    except serializers.ValidationError as exc:
        raise ValidationError({name: exc.detail}) from exc


class ListPagination(BasePagination):
    """``limit`` and ``offset`` over a bare array.

    ``X-Has-More`` says whether a row follows the page, found by fetching one
    row more than asked, so no COUNT runs. ``count=true`` asks for that COUNT,
    answered in ``X-Total-Count``. A value that cannot be read answers 400
    rather than a page the caller did not ask for.

    The queryset must have a total order - end it on a unique column - or two
    rows tied on the sort key can swap between two pages and one be skipped.
    """

    default_limit = 50
    max_limit = 1000

    def paginate_queryset(self, queryset, request, view=None):
        self.limit = _query_value(
            request,
            "limit",
            serializers.IntegerField(min_value=1, max_value=self.max_limit),
            self.default_limit,
        )
        offset = _query_value(
            request, "offset", serializers.IntegerField(min_value=0), 0
        )
        wants_total = _query_value(request, "count", serializers.BooleanField(), False)
        self.total = queryset.count() if wants_total else None
        rows = list(queryset[offset : offset + self.limit + 1])
        self.has_more = len(rows) > self.limit
        return rows[: self.limit]

    def get_paginated_response(self, data):
        headers = {HAS_MORE_HEADER: "true" if self.has_more else "false"}
        if self.total is not None:
            headers[TOTAL_COUNT_HEADER] = str(self.total)
        return Response(data, headers=headers)

    def get_paginated_response_schema(self, schema):
        return schema

    def get_schema_operation_parameters(self, view):
        return [
            {
                "name": "limit",
                "required": False,
                "in": "query",
                "description": (
                    f"Rows per page: {self.default_limit} by default, "
                    f"{self.max_limit} at most. The X-Has-More response header "
                    "says whether a row follows the page."
                ),
                "schema": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": self.max_limit,
                    "default": self.default_limit,
                },
            },
            {
                "name": "offset",
                "required": False,
                "in": "query",
                "description": "Rows to skip before the page.",
                "schema": {"type": "integer", "minimum": 0, "default": 0},
            },
            {
                "name": "count",
                "required": False,
                "in": "query",
                "description": (
                    "true to get the number of rows of the whole list in the "
                    "X-Total-Count response header."
                ),
                "schema": {"type": "boolean", "default": False},
            },
        ]


class OptInLimitOffsetPagination(LimitOffsetPagination):
    """Limit/offset slicing that only engages when the client asks for it.

    Without a ``limit`` query parameter the endpoint behaves exactly as if it
    were unpaginated and returns the full array. With one, the body is still a
    bare array (no ``{count, next, results}`` envelope), and the
    ``X-Has-More`` response header says whether another page exists past
    ``offset + limit``. Existence of a next page is detected by fetching one
    extra row, so no COUNT query is issued.

    A list that does not open on its first row - on its last page, or on the
    page holding one row - passes ``default_offset``, a function of the limit
    called only when the request names no ``offset``.
    """

    default_limit = None
    max_limit = 1000
    template = None

    def paginate_queryset(self, queryset, request, view=None, *, default_offset=None):
        self.request = request
        self.limit = self.get_limit(request)
        if self.limit is None:
            return None
        if default_offset is None or self.offset_query_param in request.query_params:
            self.offset = self.get_offset(request)
        else:
            self.offset = default_offset(self.limit)
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
