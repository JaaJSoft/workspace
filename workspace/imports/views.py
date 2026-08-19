from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .errors import ImportsError
from .providers.registry import provider_registry
from .queries import user_connections_qs, user_jobs_qs
from .serializers import (
    BrowseQuerySerializer,
    ConnectionCreateSerializer,
    ConnectionSerializer,
    ConnectionUpdateSerializer,
    JobCreateSerializer,
    JobItemSerializer,
    JobItemsQuerySerializer,
    JobSerializer,
    PageQuerySerializer,
)
from .services import connections as connections_service
from .services import jobs as jobs_service


def _bad_remote(exc: ImportsError):
    return Response({"detail": exc.user_message}, status=status.HTTP_400_BAD_REQUEST)


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
        except ImportsError as exc:
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
        except ImportsError as exc:
            return _bad_remote(exc)
        return Response(ConnectionSerializer(connection).data)

    @extend_schema(summary="Delete an import connection and its job history")
    def delete(self, request, uuid):
        connection = self._get(request, uuid)
        if connection is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            connections_service.delete_connection(connection)
        except connections_service.ConnectionBusy as exc:
            return Response(
                {"detail": exc.user_message}, status=status.HTTP_409_CONFLICT
            )
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
        except ImportsError as exc:
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
        except ImportsError as exc:
            return _bad_remote(exc)
        return Response(
            {
                "path": query.validated_data["path"],
                "entries": [e.as_dict() for e in entries],
            }
        )


@extend_schema(tags=["Imports"])
class JobListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="List the user's import jobs, newest first",
        parameters=[PageQuerySerializer],
    )
    def get(self, request):
        query = PageQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        qs = user_jobs_qs(request.user).select_related("connection")
        total = qs.count()
        page = qs[params["offset"] : params["offset"] + params["limit"]]
        return Response(
            {"count": total, "results": JobSerializer(page, many=True).data}
        )

    @extend_schema(
        summary="Create and start an import job", request=JobCreateSerializer
    )
    def post(self, request):
        ser = JobCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        connection = (
            user_connections_qs(request.user)
            .filter(uuid=ser.validated_data["connection"])
            .first()
        )
        if connection is None:
            return Response(
                {"connection": ["Not found."]}, status=status.HTTP_400_BAD_REQUEST
            )
        try:
            job = jobs_service.create_job(
                request.user,
                connection,
                ser.validated_data["kinds"],
                ser.validated_data["options"],
            )
        except jobs_service.InvalidJobOptions as exc:
            return Response({"options": exc.errors}, status=status.HTTP_400_BAD_REQUEST)
        except jobs_service.JobAlreadyRunning as exc:
            return Response(
                {"detail": exc.user_message}, status=status.HTTP_409_CONFLICT
            )
        except ImportsError as exc:
            return _bad_remote(exc)
        return Response(JobSerializer(job).data, status=status.HTTP_201_CREATED)


class _JobView(APIView):
    permission_classes = [IsAuthenticated]

    def _get(self, request, uuid):
        return (
            user_jobs_qs(request.user)
            .select_related("connection")
            .filter(uuid=uuid)
            .first()
        )


@extend_schema(tags=["Imports"])
class JobDetailView(_JobView):
    @extend_schema(summary="Get an import job with its progress")
    def get(self, request, uuid):
        job = self._get(request, uuid)
        if job is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(JobSerializer(job).data)


@extend_schema(tags=["Imports"])
class JobItemsView(_JobView):
    @extend_schema(
        summary="List the entries a job processed (filter by status for the error report)",
        parameters=[JobItemsQuerySerializer],
    )
    def get(self, request, uuid):
        job = self._get(request, uuid)
        if job is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        query = JobItemsQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        qs = job.items.order_by("created_at")
        if params.get("status"):
            qs = qs.filter(status=params["status"])
        total = qs.count()
        page = qs[params["offset"] : params["offset"] + params["limit"]]
        return Response(
            {"count": total, "results": JobItemSerializer(page, many=True).data}
        )


@extend_schema(tags=["Imports"])
class JobCancelView(_JobView):
    @extend_schema(summary="Ask a job to stop; what is already imported stays")
    def post(self, request, uuid):
        job = self._get(request, uuid)
        if job is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            job = jobs_service.cancel_job(job)
        except ImportsError as exc:
            return _bad_remote(exc)
        return Response(JobSerializer(job).data)


@extend_schema(tags=["Imports"])
class JobRetryView(_JobView):
    @extend_schema(
        summary="Start a new job with the same settings, skipping what is already done"
    )
    def post(self, request, uuid):
        job = self._get(request, uuid)
        if job is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            new_job = jobs_service.retry_job(job)
        except jobs_service.JobAlreadyRunning as exc:
            return Response(
                {"detail": exc.user_message}, status=status.HTTP_409_CONFLICT
            )
        except ImportsError as exc:
            return _bad_remote(exc)
        return Response(JobSerializer(new_job).data, status=status.HTTP_201_CREATED)
