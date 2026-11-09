"""Copy action for FileViewSet."""

from django.utils.translation import gettext as _
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from workspace.common.uuids import parse_uuid_or_none
from workspace.files.models import File
from workspace.files.serializers import (
    FileContentStale,
    FileLocked,
    FileSerializer,
    locked_payload,
)
from workspace.files.services import FileService
from workspace.files.services.transfers import (
    Outcome,
    Refusal,
    TransferRefused,
    copy_item,
)

_ON_CONFLICT = ("rename", "replace", "error")


def _refusal_response(refusal):
    """The answer each refusal of a copy owes the client."""
    reason = refusal.reason
    if reason == Refusal.QUOTA:
        # Already the 413 the client expects.
        raise refusal.__cause__
    if reason == Refusal.LOCKED:
        raise FileLocked(locked_payload(refusal.holder))
    if reason == Refusal.STALE:
        raise FileContentStale(
            {
                "detail": FileContentStale.default_detail,
                "content_hash": refusal.current_hash,
            }
        )
    if reason == Refusal.NOT_REPLACEABLE:
        raise PermissionDenied(_("You cannot replace the existing file."))
    if reason == Refusal.NAME_TAKEN:
        return Response(
            {"name": [_("A file with the same name already exists in this folder.")]},
            status=status.HTTP_400_BAD_REQUEST,
        )
    forbidden = {
        Refusal.NOT_ALLOWED: _("Copying this item is not allowed."),
        Refusal.QUARANTINED: _("File is quarantined."),
    }
    if reason in forbidden:
        return Response({"detail": forbidden[reason]}, status=status.HTTP_403_FORBIDDEN)
    bad_request = {
        # Same answer whether the folder is missing or out of reach, so its
        # existence does not leak.
        Refusal.TARGET: _("Parent folder not found."),
        Refusal.INTO_ITSELF: _("Cannot copy folder into itself or its descendants."),
        Refusal.NO_CONTENT: _(
            "This file has no content to replace the existing one with."
        ),
    }
    return Response({"detail": bad_request[reason]}, status=status.HTTP_400_BAD_REQUEST)


class CopyMixin:
    """Adds the copy action."""

    @extend_schema(
        summary="Copy file or folder",
        description=(
            "Create a copy of a file or folder. For folders, copies recursively. "
            "The copy is placed in the specified parent folder (or root if not specified)."
        ),
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "parent": {
                        "type": "string",
                        "format": "uuid",
                        "nullable": True,
                        "description": "Target parent folder UUID (null for root)",
                    },
                    "on_conflict": {
                        "type": "string",
                        "enum": ["rename", "replace", "error"],
                        "default": "rename",
                        "description": (
                            "What to do when the target folder already holds "
                            "a file with the same name: 'rename' stores the "
                            "copy as 'name (Copy).ext' (default, and always the "
                            "case for folders and for a copy into the file's own "
                            "folder), 'replace' writes the content into the "
                            "existing file and answers 200 with it, 'error' "
                            "rejects the request."
                        ),
                    },
                },
            },
        },
        responses={
            200: OpenApiResponse(
                response=FileSerializer,
                description="on_conflict='replace' matched an existing file: its content was replaced and it is returned instead.",
            ),
            201: OpenApiResponse(
                response=FileSerializer,
                description="Copied file or folder.",
            ),
            400: OpenApiResponse(description="Invalid request."),
        },
    )
    @action(detail=True, methods=["post"], url_path="copy")
    def copy(self, request, uuid=None):
        """Copy a file or folder to a new location."""
        file_obj = self.get_object()

        parent = None
        parent_uuid = request.data.get("parent")
        if parent_uuid:
            parsed_parent_uuid = parse_uuid_or_none(parent_uuid)
            if parsed_parent_uuid is not None:
                parent = File.objects.filter(
                    uuid=parsed_parent_uuid,
                    node_type=File.NodeType.FOLDER,
                    deleted_at__isnull=True,
                ).first()
            if parent is None:
                return Response(
                    {"detail": _("Parent folder not found.")},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        on_conflict = request.data.get("on_conflict", "rename")
        if on_conflict not in _ON_CONFLICT:
            return Response(
                {"on_conflict": "Must be one of: rename, replace, error."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = copy_item(request.user, file_obj, parent, on_conflict)
        except TransferRefused as refusal:
            return _refusal_response(refusal)

        # get_queryset() only spans the user's own personal files; a node in a
        # group folder needs the access-aware annotation instead to carry what
        # FileSerializer requires.
        annotated = FileService.annotate_for_serializer(
            File.objects.filter(pk=result.node.pk), request.user
        ).first()
        replaced = result.outcome == Outcome.REPLACED
        return Response(
            self.get_serializer(annotated).data,
            status=status.HTTP_200_OK if replaced else status.HTTP_201_CREATED,
        )
