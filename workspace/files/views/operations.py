"""Batch file operations: asked for here, carried out by a worker."""

from django.utils.translation import gettext
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.files.services import operations

from ..models import FileOperation
from ..serializers import FileOperationCreateSerializer, FileOperationSerializer
from ..tasks import run_file_operation

# The operations panel shows a session's worth of operations, not a history.
_LIST_LIMIT = 50


def _operation_or_none(user, uuid):
    return FileOperation.objects.filter(pk=uuid, owner=user).first()


def _not_found():
    return Response(
        {"detail": gettext("No such operation.")}, status=status.HTTP_404_NOT_FOUND
    )


@extend_schema(tags=["Files"])
class FileOperationListView(APIView):
    @extend_schema(
        summary="List batch operations",
        description=(
            "The caller's operations, most recent first: those still running "
            "and the reports of the finished ones not dismissed yet."
        ),
        responses=FileOperationSerializer(many=True),
    )
    def get(self, request):
        listed = FileOperation.objects.filter(owner=request.user)[:_LIST_LIMIT]
        return Response(FileOperationSerializer(listed, many=True).data)

    @extend_schema(
        summary="Start a batch operation",
        description=(
            "Move, copy, trash, restore, purge or extract the given items, "
            "or empty the caller's trash. This is the only way to do any of "
            "these. The request only records the batch: a worker goes "
            "through it one item at a time, and progress reaches the caller "
            "as 'operation' events on the files event stream. An item that "
            "cannot go through - out of reach, locked, name taken - is "
            "reported in 'errors' and the rest carries on."
        ),
        request=FileOperationCreateSerializer,
        responses={
            202: FileOperationSerializer,
            400: OpenApiResponse(description="Invalid request."),
            429: OpenApiResponse(description="Too many operations in progress."),
        },
    )
    def post(self, request):
        serializer = FileOperationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            operation = operations.create_operation(
                request.user, data["kind"], data["items"], data.get("target")
            )
        except operations.TargetUnavailable as error:
            # Same answer whether the folder is missing or out of reach.
            raise ValidationError({"target": [gettext("Folder not found.")]}) from error
        except operations.TooManyOperations:
            return Response(
                {
                    "detail": gettext(
                        "Too many operations in progress. Wait for one to finish."
                    )
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        try:
            run_file_operation.delay(str(operation.pk))
        except Exception:
            # Nobody will ever run it: do not leave it looking pending.
            operation.delete()
            raise
        # With an eager worker it is already over.
        operation.refresh_from_db()
        return Response(
            FileOperationSerializer(operation).data, status=status.HTTP_202_ACCEPTED
        )


@extend_schema(tags=["Files"])
class FileOperationView(APIView):
    @extend_schema(
        summary="Read a batch operation",
        responses={
            200: FileOperationSerializer,
            404: OpenApiResponse(description="No such operation."),
        },
    )
    def get(self, request, uuid):
        operation = _operation_or_none(request.user, uuid)
        if operation is None:
            return _not_found()
        return Response(FileOperationSerializer(operation).data)

    @extend_schema(
        summary="Dismiss a finished batch operation",
        responses={
            204: OpenApiResponse(description="Dismissed."),
            404: OpenApiResponse(description="No such operation."),
            409: OpenApiResponse(description="The operation is still in progress."),
        },
    )
    def delete(self, request, uuid):
        operation = _operation_or_none(request.user, uuid)
        if operation is None:
            return _not_found()
        dismissed, _ = (
            FileOperation.objects.filter(pk=operation.pk)
            .exclude(status__in=FileOperation.ACTIVE_STATUSES)
            .delete()
        )
        if not dismissed:
            return Response(
                {"detail": gettext("The operation is still in progress.")},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Files"])
class FileOperationCancelView(APIView):
    @extend_schema(
        summary="Cancel a batch operation",
        description=(
            "Stop the operation after the item in flight. What it already "
            "did stays done. Cancelling a finished operation changes nothing."
        ),
        request=None,
        responses={
            200: FileOperationSerializer,
            404: OpenApiResponse(description="No such operation."),
        },
    )
    def post(self, request, uuid):
        operation = _operation_or_none(request.user, uuid)
        if operation is None:
            return _not_found()
        operation = operations.request_cancel(operation)
        return Response(FileOperationSerializer(operation).data)
