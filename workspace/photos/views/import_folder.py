"""REST endpoint for the folder photos imported from the Photos page go to."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from ..services.import_folder import (
    choose_import_folder,
    ensure_import_folder,
    import_folder,
    import_folder_data,
    writable_folder,
)


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
