"""REST endpoints hiding photos, videos and folders from the caller's library.

Hiding is the caller's own business: it never touches the file, nor what
anyone else's library shows. A folder hides everything under it.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.uuids import (
    BatchTooLarge,
    MalformedUuid,
    UuidBatchError,
    parse_uuid_batch,
)

from ..queries import hidden_folders, hideable_files, still_hidden
from ..services.hidden import hidden_folder_data, hide_files, unhide_files

# A selection spanning a few long trips; the limit only bounds one request.
MAX_FILES = 2000

FILES_BODY = {
    "type": "object",
    "properties": {
        "files": {"type": "array", "items": {"type": "string", "format": "uuid"}}
    },
    "required": ["files"],
}


def _refused(detail):
    return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)


def _parse_files(data):
    """The ``files`` uuids of *data*, or a 400 Response explaining why not."""
    try:
        return parse_uuid_batch(data, key="files", max_items=MAX_FILES)
    except BatchTooLarge:
        return _refused(f"Too many files (max {MAX_FILES}).")
    except MalformedUuid:
        return _refused("Malformed UUID in files.")
    except UuidBatchError:
        return _refused("files must be a non-empty list.")


@extend_schema(tags=["Photos - Hidden"])
class HiddenFilesView(APIView):
    @extend_schema(
        summary="Hide photos or folders",
        description=(
            "Take photos and videos the caller can open, or folders of their "
            "own or of their groups, out of the caller's photo library. A "
            "folder hides everything under it. Those already hidden are "
            "skipped."
        ),
        request={"application/json": FILES_BODY},
        responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT)},
    )
    def post(self, request):
        uuids = _parse_files(request.data)
        if isinstance(uuids, Response):
            return uuids
        found = hideable_files(request.user).in_bulk(uuids)
        if len(found) != len(set(uuids)):
            return _refused("One or more files are not photos or folders you can hide.")
        return Response({"hidden": hide_files(request.user, found.values())})


@extend_schema(tags=["Photos - Hidden"])
class HiddenFilesRemoveView(APIView):
    @extend_schema(
        summary="Unhide photos or folders",
        description=(
            "Bring hidden photos, videos or folders back into the library. "
            "`still_hidden` lists those of them a hidden folder around them "
            "keeps out of it: only unhiding that folder brings them back."
        ),
        request={"application/json": FILES_BODY},
        responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT)},
    )
    def post(self, request):
        uuids = _parse_files(request.data)
        if isinstance(uuids, Response):
            return uuids
        unhidden = unhide_files(request.user, uuids)
        kept = still_hidden(request.user, uuids)
        return Response(
            {
                "unhidden": unhidden,
                "still_hidden": [
                    str(uuid) for uuid in dict.fromkeys(uuids) if uuid in kept
                ],
            }
        )


@extend_schema(tags=["Photos - Hidden"])
class HiddenFoldersView(APIView):
    @extend_schema(
        summary="List hidden folders",
        description="The folders the caller hid from their library, by path.",
        responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT)},
    )
    def get(self, request):
        return Response(
            [hidden_folder_data(folder) for folder in hidden_folders(request.user)]
        )
