"""The points of the library map (photos/ui/map.html): every located photo
of a library, for the browser to group by what crowds on screen."""

from django.http import Http404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.users.services.settings import get_user_timezone

from ..models import MediaItem
from ..queries import MINE, library_files
from ..services.library_map import map_points
from ..services.location_map import map_available
from ..services.preferences import ALL_TYPES, default_scope_token, resolve_scope


@extend_schema(tags=["Photos - Map"])
class LibraryMapView(APIView):
    @extend_schema(
        summary="Located photos of a library",
        description=(
            "Every photo and video of the library that carries a position, "
            "newest first, undated last. Each point is a list: uuid, "
            "latitude, longitude, month (year * 12 + month - 1 in the "
            "caller's timezone, null when undated), 1 for a video, the "
            "sensitivity level, and the thumbnail's edit version (null "
            "without a thumbnail, empty when unedited). 404 when the "
            "instance offers no map."
        ),
        parameters=[
            OpenApiParameter(
                "scope",
                OpenApiTypes.STR,
                description="mine, shared, all or group:<id>; the caller's default library when absent.",
            ),
            OpenApiParameter(
                "type",
                OpenApiTypes.STR,
                description="photo, video or all (the default).",
            ),
        ],
        responses={
            200: OpenApiResponse(OpenApiTypes.OBJECT),
            400: OpenApiResponse(description="Unknown media type."),
            404: OpenApiResponse(
                description="No map, or a group the caller is not in."
            ),
        },
    )
    def get(self, request):
        if not map_available():
            raise Http404
        token = request.query_params.get("scope", "")
        if token:
            scope = resolve_scope(request.user, token)
            if scope is None:
                raise Http404
        else:
            # A default group the user has left is no reason to fail.
            scope = (
                resolve_scope(request.user, default_scope_token(request.user)) or MINE
            )
        files = library_files(request.user, scope)
        media_type = request.query_params.get("type", "") or ALL_TYPES
        if media_type in MediaItem.MediaType.values:
            files = files.filter(media_item__media_type=media_type)
        elif media_type != ALL_TYPES:
            return Response(
                {"detail": "Unknown media type."}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response({"points": map_points(files, get_user_timezone(request.user))})
