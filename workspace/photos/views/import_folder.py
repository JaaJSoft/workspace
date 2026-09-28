"""REST endpoints for the folder photos imported from the Photos page go to."""

from django.db import transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.uuids import parse_uuid_or_none

from ..services.import_by_date import MAX_FILES, importable_files
from ..services.import_folder import (
    choose_import_folder,
    ensure_import_folder,
    import_folder,
    import_folder_data,
    writable_folder,
)
from ..services.preferences import import_by_date
from ..tasks import file_imports_by_date


@extend_schema(tags=["Photos - Import"])
class ImportFolderView(APIView):
    @extend_schema(
        summary="Get the import folder",
        description=(
            "The folder the Photos page imports into. `uuid` is null while the "
            "default folder has not been created yet."
        ),
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request):
        return Response(import_folder_data(import_folder(request.user)))

    @extend_schema(
        summary="Resolve the import folder for an upload",
        description="Same as GET, but creates the default folder when missing.",
        request=None,
        responses={200: OpenApiTypes.OBJECT},
    )
    def post(self, request):
        return Response(import_folder_data(ensure_import_folder(request.user)))

    @extend_schema(
        summary="Choose the import folder",
        request={
            "application/json": {
                "type": "object",
                "properties": {"folder": {"type": "string", "format": "uuid"}},
                "required": ["folder"],
            }
        },
        responses={
            200: OpenApiTypes.OBJECT,
            400: OpenApiResponse(description="Not a folder the user can add files to."),
        },
    )
    def put(self, request):
        folder = writable_folder(request.user, request.data.get("folder"))
        if folder is None:
            return Response(
                {"folder": "Choose a folder you can add files to."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        choose_import_folder(request.user, folder)
        return Response(import_folder_data(folder))


@extend_schema(tags=["Photos - Import"])
class ImportByDateView(APIView):
    @extend_schema(
        summary="Sort imported files by date",
        description=(
            "Files the Photos page just uploaded to the import folder. With the "
            "`import_by_date` preference on, each one moves to a `YYYY/MM` "
            "folder of the import folder after the day it was taken, in the "
            "background. A file no longer at the top of the import folder, "
            "or that is not the user's, is left alone."
        ),
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {"type": "string", "format": "uuid"},
                        "maxItems": MAX_FILES,
                    }
                },
                "required": ["files"],
            }
        },
        responses={
            202: OpenApiTypes.OBJECT,
            400: OpenApiResponse(description="Not a list of up to 500 file uuids."),
        },
    )
    def post(self, request):
        raw = request.data.get("files")
        if not isinstance(raw, list) or not raw or len(raw) > MAX_FILES:
            return Response(
                {"files": f"Send a list of 1 to {MAX_FILES} file uuids."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        uuids = [parse_uuid_or_none(value) for value in raw]
        if None in uuids:
            return Response(
                {"files": "Every item must be a file uuid."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        queued = 0
        if import_by_date(request.user):
            files = [
                str(pk)
                for pk in importable_files(request.user, uuids).values_list(
                    "uuid", flat=True
                )
            ]
            if files:
                user_id = request.user.pk
                transaction.on_commit(
                    lambda: file_imports_by_date.delay(user_id, files)
                )
            queued = len(files)
        return Response({"queued": queued}, status=status.HTTP_202_ACCEPTED)
