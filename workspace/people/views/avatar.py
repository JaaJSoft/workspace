from django.core.files.storage import default_storage
from django.http import FileResponse, HttpResponse
from django.utils.translation import gettext as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.generics import GenericAPIView
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import SAFE_METHODS, AllowAny
from rest_framework.response import Response

from workspace.common.mixins import CacheControlMixin

from ..models import Person
from ..queries import reachable_person
from ..serializers import AvatarUploadSerializer
from ..services import avatar as avatar_service


@extend_schema(tags=["People"])
class PersonAvatarView(CacheControlMixin, GenericAPIView):
    """GET is public like user avatars: the image URL lands in <img> tags
    that carry no auth header. Knowing a uuid is the only key, and the
    avatar reveals nothing the contact card does not."""

    queryset = Person.objects.none()
    serializer_class = AvatarUploadSerializer
    parser_classes = [MultiPartParser]
    cache_max_age = 300
    cache_stale_while_revalidate = 86400

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [AllowAny()]
        return super().get_permissions()

    def _person(self):
        person = reachable_person(self.request.user, self.kwargs["uuid"])
        if person is None:
            raise NotFound
        return person

    @extend_schema(
        summary="Get the avatar of a contact",
        responses={(200, "image/webp"): OpenApiTypes.BINARY},
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

    @extend_schema(
        summary="Set the avatar of a contact",
        description="The square named by the crop fields is kept, as a WebP image.",
        request={"multipart/form-data": AvatarUploadSerializer},
        responses={204: None},
    )
    def put(self, request, uuid):
        person = self._person()
        body = self.get_serializer(data=request.data)
        body.is_valid(raise_exception=True)
        image = body.validated_data
        try:
            avatar_service.save_avatar(
                person,
                image["file"],
                image["crop_x"],
                image["crop_y"],
                image["crop_w"],
                image["crop_h"],
            )
        except OSError, ValueError:
            raise ValidationError(
                {"file": [_("Could not process the image.")]}
            ) from None
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        summary="Remove the avatar of a contact", request=None, responses={204: None}
    )
    def delete(self, request, uuid):
        avatar_service.delete_avatar(self._person())
        return Response(status=status.HTTP_204_NO_CONTENT)
