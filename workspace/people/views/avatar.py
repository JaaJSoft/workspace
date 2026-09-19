from django.core.files.storage import default_storage
from django.http import FileResponse, HttpResponse
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import SAFE_METHODS, AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin

from ..models import Person
from ..queries import reachable_person
from ..services import avatar as avatar_service

AVATAR_MAX_SIZE = 10 * 1024 * 1024
AVATAR_ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


@extend_schema(tags=["People"])
class PersonAvatarView(CacheControlMixin, APIView):
    """GET is public like user avatars: the image URL lands in <img> tags
    that carry no auth header. Knowing a uuid is the only key, and the
    avatar reveals nothing the contact card does not."""

    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser]
    cache_max_age = 300
    cache_stale_while_revalidate = 86400

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [AllowAny()]
        return [IsAuthenticated()]

    @extend_schema(
        summary="Get a person's avatar",
        responses={200: OpenApiResponse(description="WebP image"), 404: None},
    )
    def get(self, request, uuid):
        person = Person.objects.filter(uuid=uuid, has_avatar=True).first()
        if person is None:
            return HttpResponse(status=404)
        path = avatar_service.avatar_path(person)
        if not default_storage.exists(path):
            return HttpResponse(status=404)
        etag = avatar_service.avatar_etag(person)
        if etag:
            if_none_match = request.META.get("HTTP_IF_NONE_MATCH")
            if if_none_match and if_none_match.strip('"') == etag:
                response = HttpResponse(status=304)
                response["ETag"] = f'"{etag}"'
                return response
        response = FileResponse(
            default_storage.open(path, "rb"), content_type="image/webp"
        )
        if etag:
            response["ETag"] = f'"{etag}"'
        return response

    @extend_schema(summary="Upload a person's avatar")
    def post(self, request, uuid):
        person = reachable_person(request.user, uuid)
        if person is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        image = request.FILES.get("image")
        if not image:
            return Response(
                {"errors": ["No image provided."]}, status=status.HTTP_400_BAD_REQUEST
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
            crop = [
                float(request.data.get(key, 0))
                for key in ("crop_x", "crop_y", "crop_w", "crop_h")
            ]
        except ValueError, TypeError:
            return Response(
                {"errors": ["Invalid crop coordinates."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if crop[2] <= 0 or crop[3] <= 0:
            return Response(
                {"errors": ["Invalid crop coordinates."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            avatar_service.save_avatar(person, image, *crop)
        except OSError, ValueError:
            return Response(
                {"errors": ["Could not process the image."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"message": "Avatar updated."})

    @extend_schema(summary="Delete a person's avatar")
    def delete(self, request, uuid):
        person = reachable_person(request.user, uuid)
        if person is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        avatar_service.delete_avatar(person)
        return Response(status=status.HTTP_204_NO_CONTENT)
