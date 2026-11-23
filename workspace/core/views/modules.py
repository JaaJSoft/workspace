from dataclasses import asdict

from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin
from workspace.core.module_registry import registry
from workspace.core.services.module_visibility import user_can_see_module


class ModulesView(CacheControlMixin, APIView):
    cache_max_age = 3600

    @extend_schema(
        tags=["Modules"],
        summary="List workspace modules",
        description="Returns the registered workspace modules the caller may see.",
    )
    def get(self, request):
        modules = [
            asdict(module)
            for module in registry.get_all()
            if user_can_see_module(request.user, module)
        ]
        return Response({"results": modules})
