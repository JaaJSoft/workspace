from dataclasses import asdict

from drf_spectacular.utils import extend_schema
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
