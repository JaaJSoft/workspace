"""Sync health-check views compatible with gevent workers."""

import logging

from django.core.cache import cache
from django.db import connection
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


@extend_schema_view(get=extend_schema(exclude=True))
class StartupView(APIView):
    """Startup probe: DB is reachable."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        with connection.cursor() as c:
            c.execute("SELECT 1")
        return Response({"status": "ok"})


@extend_schema_view(get=extend_schema(exclude=True))
class LiveView(APIView):
    """Liveness probe: app is not deadlocked."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response({"status": "ok"})


@extend_schema_view(get=extend_schema(exclude=True))
class ReadyView(APIView):
    """Readiness probe: DB + cache available."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        errors = {}
        try:
            with connection.cursor() as c:
                c.execute("SELECT 1")
        except Exception:
            logger.exception("Readiness probe: database check failed")
            errors["database"] = "unavailable"
        try:
            cache.set("_health", "1", 10)
            if cache.get("_health") != "1":
                raise RuntimeError("read-back failed")
        except Exception:
            logger.exception("Readiness probe: cache check failed")
            errors["cache"] = "unavailable"
        if errors:
            return Response(errors, status=500)
        return Response({"status": "ok"})
