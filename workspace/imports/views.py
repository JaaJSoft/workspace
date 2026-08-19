from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .providers.base import ProviderError
from .providers.registry import provider_registry
from .queries import user_connections_qs
from .serializers import (
    BrowseQuerySerializer,
    ConnectionCreateSerializer,
    ConnectionSerializer,
    ConnectionUpdateSerializer,
)
from .services import connections as connections_service
from .services.url_guard import UnsafeUrl


def _bad_remote(exc):
    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(tags=["Imports"])
class ProviderListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="List the import providers this server offers")
    def get(self, request):
        return Response([p.describe() for p in provider_registry.available()])


@extend_schema(tags=["Imports"])
class ConnectionListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="List the user's import connections")
    def get(self, request):
        qs = user_connections_qs(request.user)
        return Response(ConnectionSerializer(qs, many=True).data)

    @extend_schema(
        summary="Create an import connection (verified against the remote first)",
        request=ConnectionCreateSerializer,
    )
    def post(self, request):
        ser = ConnectionCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            connection = connections_service.create_connection(
                request.user, **ser.validated_data
            )
        except (ProviderError, UnsafeUrl, connections_service.UnknownProvider) as exc:
            return _bad_remote(exc)
        return Response(
            ConnectionSerializer(connection).data, status=status.HTTP_201_CREATED
        )


class _ConnectionView(APIView):
    permission_classes = [IsAuthenticated]

    def _get(self, request, uuid):
        return user_connections_qs(request.user).filter(uuid=uuid).first()


@extend_schema(tags=["Imports"])
class ConnectionDetailView(_ConnectionView):
    @extend_schema(summary="Get an import connection")
    def get(self, request, uuid):
        connection = self._get(request, uuid)
        if connection is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(ConnectionSerializer(connection).data)

    @extend_schema(
        summary="Update an import connection", request=ConnectionUpdateSerializer
    )
    def patch(self, request, uuid):
        connection = self._get(request, uuid)
        if connection is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        ser = ConnectionUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            connection = connections_service.update_connection(
                connection, **ser.validated_data
            )
        except (ProviderError, UnsafeUrl, connections_service.UnknownProvider) as exc:
            return _bad_remote(exc)
        return Response(ConnectionSerializer(connection).data)

    @extend_schema(summary="Delete an import connection and its job history")
    def delete(self, request, uuid):
        connection = self._get(request, uuid)
        if connection is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        connection.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Imports"])
class ConnectionTestView(_ConnectionView):
    @extend_schema(summary="Re-check a connection and refresh its capabilities")
    def post(self, request, uuid):
        connection = self._get(request, uuid)
        if connection is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            connection = connections_service.test_connection(connection)
        except (ProviderError, UnsafeUrl, connections_service.UnknownProvider) as exc:
            return _bad_remote(exc)
        return Response(ConnectionSerializer(connection).data)


@extend_schema(tags=["Imports"])
class ConnectionBrowseView(_ConnectionView):
    @extend_schema(
        summary="List one level of the remote tree (for the folder picker)",
        parameters=[BrowseQuerySerializer],
    )
    def get(self, request, uuid):
        connection = self._get(request, uuid)
        if connection is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        query = BrowseQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        try:
            entries = connections_service.browse_files(
                connection, query.validated_data["path"]
            )
        except (ProviderError, UnsafeUrl, connections_service.UnknownProvider) as exc:
            return _bad_remote(exc)
        return Response(
            {
                "path": query.validated_data["path"] or "/",
                "entries": [e.as_dict() for e in entries],
            }
        )
