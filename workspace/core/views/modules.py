from dataclasses import asdict

from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.cache import cached_response
from workspace.common.mixins import CacheControlMixin
from workspace.core.module_registry import registry


class ModulesView(CacheControlMixin, APIView):
    cache_max_age = 3600

    @extend_schema(
        tags=["Modules"],
        summary="List workspace modules",
        description="Returns all registered workspace modules.",
    )
    @cached_response(3600, per_user=False)
    def get(self, request):
        modules = [asdict(m) for m in registry.get_all()]
        return Response({"results": modules})
