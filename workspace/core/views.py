from dataclasses import asdict

from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.core.module_registry import registry


class ModulesView(APIView):
    @extend_schema(
        tags=['Modules'],
        summary='List workspace modules',
        description='Returns all registered workspace modules.',
    )
    def get(self, request):
        modules = [asdict(m) for m in registry.get_all()]
        return Response({'results': modules})


class UnifiedSearchView(APIView):
    @extend_schema(
        tags=['Search'],
        summary='Unified search across modules',
        description='Searches all registered module providers and returns aggregated results.',
        parameters=[
            OpenApiParameter(name='q', type=str, required=True, description='Search query (min 2 chars)'),
            OpenApiParameter(name='limit', type=int, required=False, description='Max results per provider (1-50, default 10)'),
        ],
    )
    def get(self, request):
        query = request.query_params.get('q', '').strip()
        if len(query) < 2:
            return Response({'error': 'Query must be at least 2 characters'}, status=400)

        try:
            limit = int(request.query_params.get('limit', 10))
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, 50))

        results = registry.search(query, request.user, limit)
        return Response({'query': query, 'results': results, 'count': len(results)})
