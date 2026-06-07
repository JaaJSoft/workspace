"""Extract action for FileViewSet."""

import logging

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from workspace.common.logging import scrub
from workspace.common.uuids import parse_uuid_or_none
from workspace.files.models import File
from workspace.files.services import FilePermission, FileService
from workspace.files.services.extract import (
    ArchiveTooLargeError,
    ArchiveTooManyEntriesError,
    extract_zip,
)

logger = logging.getLogger(__name__)


class ExtractMixin:
    """Adds the extract action for ZIP archives."""

    @extend_schema(
        summary="Extract a ZIP archive",
        description="Extract the contents of a ZIP file into the given destination folder.",
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "destination_uuid": {
                        "type": "string",
                        "format": "uuid",
                        "nullable": True,
                        "description": (
                            "Folder UUID to extract into. Use null to extract "
                            "into the user's root folder (no parent)."
                        ),
                    },
                },
            },
        },
        responses={
            200: OpenApiResponse(description="Archive extracted."),
            400: OpenApiResponse(description="Invalid request or corrupted archive."),
            404: OpenApiResponse(description="Source or destination not found."),
            413: OpenApiResponse(description="Archive exceeds the size limit."),
        },
    )
    @action(detail=True, methods=["post"], url_path="extract")
    def extract(self, request, uuid=None):
        file_obj = self.get_object()

        body = request.data
        if "destination_uuid" not in body:
            return Response(
                {"detail": "destination_uuid is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        raw = body.get("destination_uuid")

        dest = None
        if raw is not None:
            dest_uuid = parse_uuid_or_none(raw)
            if dest_uuid is None:
                return Response(
                    {"detail": "destination_uuid is malformed."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            dest = File.objects.filter(
                uuid=dest_uuid,
                node_type=File.NodeType.FOLDER,
                deleted_at__isnull=True,
            ).first()
            if dest is None:
                return Response(
                    {"detail": "Destination folder not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            perm = FileService.get_permission(request.user, dest)
            if perm is None or perm < FilePermission.EDIT:
                return Response(
                    {"detail": "Destination folder not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

        try:
            result = extract_zip(file_obj, dest, acting_user=request.user)
        except ArchiveTooLargeError as e:
            logger.info(
                "Extract rejected (too large) for %s: %s",
                scrub(str(file_obj.uuid)),
                scrub(str(e)),
            )
            return Response(
                {"detail": "Archive too large."},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        except ArchiveTooManyEntriesError as e:
            logger.info(
                "Extract rejected (too many entries) for %s: %s",
                scrub(str(file_obj.uuid)),
                scrub(str(e)),
            )
            return Response(
                {"detail": "Too many entries in archive."},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        except ValueError as e:
            msg = str(e)
            dest_uuid_for_log = str(dest.uuid) if dest is not None else "root"
            logger.info(
                "Extract rejected for %s into %s: %s",
                scrub(str(file_obj.uuid)),
                scrub(dest_uuid_for_log),
                scrub(msg),
            )
            # Return generic detail strings - the precise reason stays in the
            # logs (scrubbed) so internal text (e.g. entry names from a
            # malicious archive) is never echoed to the client.
            # 'Archive content missing' -> 404 (the source blob has vanished -
            # matches the chat/mail attachment convention for vanished blobs).
            if msg == "Archive content missing":
                return Response(
                    {"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND
                )
            return Response(
                {"detail": "Invalid request."}, status=status.HTTP_400_BAD_REQUEST
            )

        return Response(result, status=status.HTTP_200_OK)
