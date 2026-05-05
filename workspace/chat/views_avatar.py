import logging

from django.core.files.storage import default_storage
from django.http import FileResponse, HttpResponse
from drf_spectacular.utils import extend_schema, inline_serializer, OpenApiResponse
from rest_framework import serializers, status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Conversation
from .services import avatar as group_avatar_service
from .services.conversations import get_active_membership

logger = logging.getLogger(__name__)


AVATAR_MAX_SIZE = 10 * 1024 * 1024  # 10 MB
AVATAR_ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


@extend_schema(tags=['Chat'])
class GroupAvatarUploadView(APIView):
    """Upload or delete a group conversation's avatar."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    @extend_schema(
        summary="Upload group avatar",
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "image": {"type": "string", "format": "binary"},
                    "crop_x": {"type": "number"},
                    "crop_y": {"type": "number"},
                    "crop_w": {"type": "number"},
                    "crop_h": {"type": "number"},
                },
                "required": ["image", "crop_x", "crop_y", "crop_w", "crop_h"],
            }
        },
        responses={
            200: inline_serializer(
                name="GroupAvatarUploadResponse",
                fields={"message": serializers.CharField()},
            ),
            400: OpenApiResponse(description="Validation error"),
            403: OpenApiResponse(description="Forbidden"),
        },
    )
    def post(self, request, conversation_id):
        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        conversation = Conversation.objects.get(pk=conversation_id)
        if conversation.kind != Conversation.Kind.GROUP:
            return Response(
                {'detail': 'Avatars are only supported for group conversations.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        image = request.FILES.get("image")
        if not image:
            return Response(
                {"errors": ["No image provided."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if image.content_type not in AVATAR_ALLOWED_TYPES:
            return Response(
                {"errors": ["Unsupported image type. Use JPEG, PNG, WebP, or GIF."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if image.size > AVATAR_MAX_SIZE:
            return Response(
                {"errors": ["Image too large. Maximum size is 10 MB."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            crop_x = float(request.data.get("crop_x", 0))
            crop_y = float(request.data.get("crop_y", 0))
            crop_w = float(request.data.get("crop_w", 0))
            crop_h = float(request.data.get("crop_h", 0))
        except (TypeError, ValueError):
            return Response(
                {"errors": ["Invalid crop coordinates."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if crop_w <= 0 or crop_h <= 0:
            return Response(
                {"errors": ["Crop width and height must be positive."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        group_avatar_service.process_and_save_group_avatar(
            conversation, image, crop_x, crop_y, crop_w, crop_h,
        )
        return Response({"message": "Group avatar updated successfully."})

    @extend_schema(
        summary="Delete group avatar",
        responses={
            200: inline_serializer(
                name="GroupAvatarDeleteResponse",
                fields={"message": serializers.CharField()},
            ),
            403: OpenApiResponse(description="Forbidden"),
        },
    )
    def delete(self, request, conversation_id):
        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        conversation = Conversation.objects.get(pk=conversation_id)
        if conversation.kind != Conversation.Kind.GROUP:
            return Response(
                {'detail': 'Avatars are only supported for group conversations.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        group_avatar_service.delete_group_avatar(conversation)
        return Response({"message": "Group avatar removed."})


@extend_schema(tags=['Chat'])
class GroupAvatarRetrieveView(APIView):
    """Serve a group conversation's avatar image (public)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        summary="Get group avatar",
        responses={
            200: OpenApiResponse(description="Avatar image (WebP)"),
            304: OpenApiResponse(description="Not modified"),
            404: OpenApiResponse(description="No avatar found"),
        },
    )
    def get(self, request, conversation_id):
        path = group_avatar_service.get_group_avatar_path(conversation_id)

        etag = group_avatar_service.get_group_avatar_etag(conversation_id)
        if etag:
            if_none_match = request.META.get("HTTP_IF_NONE_MATCH")
            if if_none_match and if_none_match.strip('"') == etag:
                response = HttpResponse(status=304)
                response["ETag"] = f'"{etag}"'
                return response

        # Open directly and trust the storage to raise on missing file -
        # avoids a TOCTOU race between exists() and open().
        try:
            avatar_file = default_storage.open(path, "rb")
        except (FileNotFoundError, OSError):
            return HttpResponse(status=404)
        response = FileResponse(avatar_file, content_type="image/webp")
        response["Cache-Control"] = "no-cache"
        if etag:
            response["ETag"] = f'"{etag}"'
        return response
