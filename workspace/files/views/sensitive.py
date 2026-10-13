"""Marking files sensitive: their preview is blurred for everyone who can
open them (see files/sensitivity.py)."""

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

from ..actions import ActionRegistry
from ..models import File
from ..sensitivity import mark_sensitive, with_sensitivity
from ..services import FileService

# A selection of a few long trips; the limit only bounds one request.
MAX_FILES = 2000


def _refused(detail, code=status.HTTP_400_BAD_REQUEST):
    return Response({"detail": detail}, status=code)


@extend_schema(tags=["Files"])
class SensitiveFilesView(APIView):
    @extend_schema(
        summary="Mark files sensitive or not",
        description=(
            "Blur the previews of photos and videos for everyone who can open "
            "them (`sensitive: true`), or stop blurring them (`false`). The "
            "choice wins over automatic detection. Needs edit rights on every "
            "file; nothing is marked when one of them is refused."
        ),
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "uuids": {
                        "type": "array",
                        "items": {"type": "string", "format": "uuid"},
                    },
                    "sensitive": {"type": "boolean"},
                },
                "required": ["uuids", "sensitive"],
            }
        },
        responses={
            200: OpenApiResponse(response=OpenApiTypes.OBJECT),
            400: OpenApiResponse(description="Invalid request."),
            403: OpenApiResponse(description="A file cannot be marked."),
            404: OpenApiResponse(description="A file was not found."),
        },
    )
    def post(self, request):
        try:
            uuids = parse_uuid_batch(request.data, key="uuids", max_items=MAX_FILES)
        except BatchTooLarge:
            return _refused(f"Too many files (max {MAX_FILES}).")
        except MalformedUuid:
            return _refused("Malformed UUID in uuids.")
        except UuidBatchError:
            return _refused("uuids must be a non-empty list.")
        sensitive = request.data.get("sensitive")
        if not isinstance(sensitive, bool):
            return _refused("sensitive must be true or false.")

        files = list(
            with_sensitivity(
                File.objects.filter(
                    FileService.accessible_files_q(request.user), uuid__in=uuids
                )
            ).distinct()
        )
        if len(files) != len(set(uuids)):
            return _refused("One or more files not found.", status.HTTP_404_NOT_FOUND)
        permissions = FileService.get_permissions_bulk(request.user, files)
        for file_obj in files:
            if not ActionRegistry.is_action_available(
                "toggle_sensitive",
                request.user,
                file_obj,
                permission=permissions[file_obj.pk],
            ):
                return _refused(
                    "One or more files cannot be marked.", status.HTTP_403_FORBIDDEN
                )
        marked = mark_sensitive([file_obj.pk for file_obj in files], sensitive)
        return Response({"marked": marked, "sensitive": sensitive})
