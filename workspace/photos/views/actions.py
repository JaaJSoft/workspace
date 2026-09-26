"""What the caller may do with each album of a batch.

Modelled on the vault endpoint rather than the projects one: an album out of
reach and a uuid naming nothing both come back as an empty list inside a 200,
so one unreachable album never costs the rest of the batch its answer, and
the two cases read the same.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin
from workspace.common.uuids import (
    BatchTooLarge,
    MalformedUuid,
    UuidBatchError,
    parse_uuid_batch,
)

from ..actions import AlbumActionRegistry
from ..queries import album_roles, user_albums

MAX_BATCH = 200


def _refused(detail):
    return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=["Photos - Albums"],
    summary="Get available actions for albums",
    description=(
        "Return the actions the caller may take on each of a batch of album "
        "UUIDs. Every submitted UUID gets a key, spelled as it was sent; one "
        "the caller cannot reach gets an empty list."
    ),
    request={
        "application/json": {
            "type": "object",
            "properties": {
                "uuids": {
                    "type": "array",
                    "items": {"type": "string", "format": "uuid"},
                },
            },
            "required": ["uuids"],
        },
    },
    responses={
        200: OpenApiResponse(
            response=OpenApiTypes.OBJECT,
            description="Map of UUID to the list of available actions.",
        ),
        400: OpenApiResponse(description="Malformed or oversized batch."),
    },
)
class AlbumActionsView(CacheControlMixin, APIView):
    cache_no_store = True

    def post(self, request):
        try:
            parsed = parse_uuid_batch(request.data, max_items=MAX_BATCH)
        except BatchTooLarge:
            return _refused(f"Too many UUIDs (max {MAX_BATCH}).")
        except MalformedUuid:
            return _refused("Malformed UUID in uuids.")
        except UuidBatchError:
            return _refused("uuids must be a non-empty list.")

        spellings = {}
        for item, value in zip(request.data["uuids"], parsed, strict=True):
            spellings.setdefault(value, []).append(str(item))
        result = {key: [] for keys in spellings.values() for key in keys}

        albums = list(user_albums(request.user).filter(uuid__in=parsed))
        roles = album_roles(request.user, albums)
        for album in albums:
            actions = AlbumActionRegistry.get_available_actions(
                request.user, album, role=roles.get(album.uuid)
            )
            for key in spellings[album.uuid]:
                result[key] = actions
        return Response(result)
