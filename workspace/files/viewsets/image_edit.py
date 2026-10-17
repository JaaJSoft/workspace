"""Rotate and crop actions for FileViewSet (services/image_edits.py)."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from workspace.files.serializers import (
    FileContentStale,
    ImageEditSerializer,
    ImageRotateSerializer,
    locked_payload,
)
from workspace.files.services.image_edits import (
    edit_version,
    rotate_edit,
    save_image_edit,
)
from workspace.files.services.locking import LockConflict, StaleContent

_EDIT_RESPONSES = {
    200: OpenApiResponse(
        description=(
            "The edit now shown (`image_edit`, null when back to the original) "
            "and the token the thumbnail URL carries for it (`version`)."
        )
    ),
    400: OpenApiResponse(description="Invalid rotation or crop."),
    403: OpenApiResponse(description="The file cannot be edited by this user."),
    404: OpenApiResponse(description="File not found."),
    412: OpenApiResponse(description="The file's content changed meanwhile."),
    423: OpenApiResponse(description="The file is locked by another user."),
}


class ImageEditMixin:
    """Adds image_edit and image_edit_rotate actions."""

    def _editable_image(self, request, uuid):
        """The file behind *uuid* and None, or None and the refusal."""
        from workspace.files.actions import ActionRegistry

        file_obj, perm = self._resolve_file_with_access(uuid)
        if not ActionRegistry.is_action_available(
            "edit_image", request.user, file_obj, permission=perm
        ):
            return None, Response(status=status.HTTP_403_FORBIDDEN)
        return file_obj, None

    @staticmethod
    def _store(request, file_obj, edit):
        try:
            save_image_edit(file_obj, edit, acting_user=request.user)
        except LockConflict as exc:
            return Response(locked_payload(exc.holder), status=423)
        except StaleContent:
            raise FileContentStale() from None
        return Response(
            {"image_edit": file_obj.image_edit, "version": edit_version(edit)}
        )

    @extend_schema(
        summary="Rotate and crop an image, or revert it to the original",
        description=(
            "PUT stores the rotation (clockwise, after the EXIF orientation) and "
            "the crop (fractions of the rotated image) shown in place of the "
            "image; the file's bytes are never rewritten. DELETE reverts to the "
            "original."
        ),
        request=ImageEditSerializer,
        responses=_EDIT_RESPONSES,
    )
    @action(detail=True, methods=["put", "delete"], url_path="image-edit")
    def image_edit(self, request, uuid=None):
        file_obj, refusal = self._editable_image(request, uuid)
        if refusal is not None:
            return refusal
        if request.method == "DELETE":
            return self._store(request, file_obj, None)
        serializer = ImageEditSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._store(request, file_obj, serializer.validated_data["edit"])

    @extend_schema(
        summary="Rotate an image a quarter turn",
        description=(
            "Turns the image's current edit, crop included, a quarter turn "
            "clockwise (1) or anticlockwise (-1)."
        ),
        request=ImageRotateSerializer,
        responses=_EDIT_RESPONSES,
    )
    @action(detail=True, methods=["post"], url_path="image-edit/rotate")
    def image_edit_rotate(self, request, uuid=None):
        file_obj, refusal = self._editable_image(request, uuid)
        if refusal is not None:
            return refusal
        serializer = ImageRotateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        turns = serializer.validated_data["quarter_turns"]
        return self._store(request, file_obj, rotate_edit(file_obj.image_edit, turns))
