from rest_framework.filters import BaseFilterBackend
from rest_framework.settings import api_settings

from workspace.common.search import apply_fulltext

from .services.search_index import FILES_FTS


class FileSearchFilter(BaseFilterBackend):
    """Rank files by full-text relevance over their name and text content.

    Replaces DRF's SearchFilter, which could only match the columns of the
    row: a note's text lives in file storage, so `?search=` had no way to see
    it. Declare this backend AFTER the ordering filter - it has to get the
    last word on order_by to sort by relevance, and the ordering filter
    always applies the view's default ordering otherwise.
    """

    search_param = "search"

    def filter_queryset(self, request, queryset, view):
        term = (request.query_params.get(self.search_param) or "").strip()
        if not term:
            return queryset
        queryset = apply_fulltext(queryset, term, index=FILES_FTS)
        if request.query_params.get(api_settings.ORDERING_PARAM):
            # An explicit ordering is the user's choice; relevance is only the
            # default sort for a search.
            return queryset
        return queryset.order_by("-search_rank", "-updated_at")

    def get_schema_operation_parameters(self, view):
        return [
            {
                "name": self.search_param,
                "required": False,
                "in": "query",
                "description": "Full-text search over file names and text content.",
                "schema": {"type": "string"},
            }
        ]
