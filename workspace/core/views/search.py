from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.cache import cached_response
from workspace.common.limits import clamp_limit
from workspace.common.mixins import CacheControlMixin
from workspace.core.module_registry import SearchScope
from workspace.core.services import search as search_service


class UnifiedSearchView(CacheControlMixin, APIView):
    @extend_schema(
        tags=["Search"],
        summary="Unified search across modules",
        description="Searches all registered module providers and returns aggregated results.",
        parameters=[
            OpenApiParameter(
                name="q",
                type=str,
                required=True,
                description="Search query (min 2 chars)",
            ),
            OpenApiParameter(
                name="limit",
                type=int,
                required=False,
                description="Max results per provider (1-50, default 10)",
            ),
            OpenApiParameter(
                name="scope",
                type=str,
                required=False,
                enum=list(SearchScope.CHOICES),
                description=(
                    "`mine` drops what the user reaches through a group, a share "
                    "or someone else's project; anything else searches all"
                ),
            ),
        ],
    )
    @cached_response(120)
    def get(self, request):
        query = request.query_params.get("q", "").strip()
        if len(query) < search_service.MIN_QUERY_LENGTH:
            return Response(
                {
                    "error": f"Query must be at least {search_service.MIN_QUERY_LENGTH} characters"
                },
                status=400,
            )

        limit = clamp_limit(
            request.query_params.get("limit"),
            default=search_service.DEFAULT_LIMIT,
            maximum=search_service.MAX_LIMIT,
        )
        # The scope travels in the query string rather than being read from
        # the user's setting: @cached_response keys on the query params.
        scope = request.query_params.get("scope")
        if scope not in SearchScope.CHOICES:
            scope = SearchScope.ALL
        results = search_service.search_modules(query, request.user, limit, scope=scope)
        commands = search_service.search_commands(query, request.user)

        return Response(
            {
                "query": query,
                "commands": commands,
                "results": results,
                "count": len(commands) + len(results),
            }
        )
