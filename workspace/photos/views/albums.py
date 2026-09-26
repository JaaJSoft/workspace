"""REST endpoints for albums: the albums themselves, their items and order.

Every write is gated by the album action registry (``is_action_available``),
the same answer ``POST /api/v1/photos/albums/actions`` gives the client, so
a menu never offers what the endpoint refuses. An album the caller cannot
open is a 404, never a 403: the two cases must read the same.
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
    parse_uuid_or_none,
)

from ..actions import AlbumActionRegistry
from ..queries import (
    ALL,
    album_files,
    get_album_role,
    library_files,
    reachable_album,
)
from ..serializers import AlbumSerializer, AlbumWriteSerializer
from ..services.album_cards import album_cards, user_album_cards
from ..services.albums import add_items, create_album, move_items, remove_items

# A selection spanning a few long trips; the limit only bounds one request.
MAX_FILES = 2000

# The action each writable field of an album answers to.
FIELD_ACTIONS = {
    "title": "rename",
    "description": "edit_description",
    "sort_mode": "change_sort",
    "cover": "set_cover",
}

FILES_BODY = {
    "type": "object",
    "properties": {
        "files": {"type": "array", "items": {"type": "string", "format": "uuid"}}
    },
    "required": ["files"],
}


def _refused(detail):
    return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)


def _not_found():
    return Response(status=status.HTTP_404_NOT_FOUND)


def _allowed(action_id, user, album):
    return AlbumActionRegistry.is_action_available(
        action_id, user, album, role=get_album_role(user, album)
    )


def _parse_files(data, *, required=True):
    """The ``files`` uuids of *data*, or a 400 Response explaining why not."""
    if not required and (not isinstance(data, dict) or "files" not in data):
        return []
    try:
        return parse_uuid_batch(data, key="files", max_items=MAX_FILES)
    except BatchTooLarge:
        return _refused(f"Too many files (max {MAX_FILES}).")
    except MalformedUuid:
        return _refused("Malformed UUID in files.")
    except UuidBatchError:
        return _refused("files must be a non-empty list.")


def _library_files(user, uuids):
    """The photos *uuids* names in the caller's library, in that order, or
    None when one of them is not a photo or video the caller can open."""
    found = library_files(user, ALL).in_bulk(uuids)
    unique = list(dict.fromkeys(uuids))
    if len(found) != len(unique):
        return None
    return [found[uuid] for uuid in unique]


def _card(user, album):
    return album_cards(user, [album])[0]


@extend_schema(tags=["Photos - Albums"])
class AlbumListView(APIView):
    @extend_schema(
        summary="List albums",
        description="The albums the caller can open, by title.",
        responses=AlbumSerializer(many=True),
    )
    def get(self, request):
        cards = user_album_cards(request.user)
        return Response(AlbumSerializer(cards, many=True).data)

    @extend_schema(
        summary="Create an album",
        description=(
            "Create a personal album. `files`, optional, fills it with photos "
            "and videos from the caller's library, in that order."
        ),
        request=AlbumWriteSerializer,
        responses={201: AlbumSerializer},
    )
    def post(self, request):
        serializer = AlbumWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        uuids = _parse_files(request.data, required=False)
        if isinstance(uuids, Response):
            return uuids
        files = _library_files(request.user, uuids)
        if files is None:
            return _refused("One or more files are not photos you can open.")
        album = create_album(request.user, files=files, **serializer.validated_data)
        return Response(
            AlbumSerializer(_card(request.user, album)).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Photos - Albums"])
class AlbumDetailView(APIView):
    @extend_schema(summary="Get an album", responses=AlbumSerializer)
    def get(self, request, uuid):
        album = reachable_album(request.user, uuid)
        if album is None:
            return _not_found()
        return Response(AlbumSerializer(_card(request.user, album)).data)

    @extend_schema(
        summary="Update an album",
        description=(
            "Rename it, change its description or its sort mode, or pick its "
            "cover (`cover`: the uuid of one of its photos, or null to fall "
            "back to the most recently added one)."
        ),
        request=AlbumWriteSerializer,
        responses=AlbumSerializer,
    )
    def patch(self, request, uuid):
        album = reachable_album(request.user, uuid)
        if album is None:
            return _not_found()
        data = request.data if isinstance(request.data, dict) else {}
        for field, action_id in FIELD_ACTIONS.items():
            if field in data and not _allowed(action_id, request.user, album):
                return Response(status=status.HTTP_403_FORBIDDEN)

        serializer = AlbumWriteSerializer(album, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        fields = list(serializer.validated_data)
        for field, value in serializer.validated_data.items():
            setattr(album, field, value)

        if "cover" in data:
            if data["cover"] is None:
                album.cover = None
            else:
                cover_uuid = parse_uuid_or_none(data["cover"])
                cover = None
                if cover_uuid is not None:
                    cover = (
                        album_files(request.user, album).filter(uuid=cover_uuid).first()
                    )
                if cover is None:
                    return _refused("The cover must be a photo of the album.")
                album.cover = cover
            fields.append("cover")

        if fields:
            album.save(update_fields=[*fields, "updated_at"])
        return Response(AlbumSerializer(_card(request.user, album)).data)

    @extend_schema(
        summary="Delete an album",
        description="The photos stay where they are in Files.",
    )
    def delete(self, request, uuid):
        album = reachable_album(request.user, uuid)
        if album is None:
            return _not_found()
        if not _allowed("delete", request.user, album):
            return Response(status=status.HTTP_403_FORBIDDEN)
        album.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Photos - Albums"])
class AlbumItemsView(APIView):
    @extend_schema(
        summary="Add photos to an album",
        description=(
            "Append photos and videos of the caller's library to the album. "
            "Those already in it are skipped."
        ),
        request={"application/json": FILES_BODY},
        responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT)},
    )
    def post(self, request, uuid):
        album = reachable_album(request.user, uuid)
        if album is None:
            return _not_found()
        if not _allowed("add_items", request.user, album):
            return Response(status=status.HTTP_403_FORBIDDEN)
        uuids = _parse_files(request.data)
        if isinstance(uuids, Response):
            return uuids
        files = _library_files(request.user, uuids)
        if files is None:
            return _refused("One or more files are not photos you can open.")
        added = add_items(album, files, added_by=request.user)
        return Response({"added": added})


@extend_schema(tags=["Photos - Albums"])
class AlbumItemsRemoveView(APIView):
    @extend_schema(
        summary="Remove photos from an album",
        description="The files themselves stay where they are in Files.",
        request={"application/json": FILES_BODY},
        responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT)},
    )
    def post(self, request, uuid):
        album = reachable_album(request.user, uuid)
        if album is None:
            return _not_found()
        if not _allowed("remove_items", request.user, album):
            return Response(status=status.HTTP_403_FORBIDDEN)
        uuids = _parse_files(request.data)
        if isinstance(uuids, Response):
            return uuids
        return Response({"removed": remove_items(album, uuids)})


@extend_schema(tags=["Photos - Albums"])
class AlbumReorderView(APIView):
    @extend_schema(
        summary="Reorder an album",
        description=(
            "Move photos of a manually sorted album, in the given order, "
            "right `before` or right `after` another of its photos, or to the "
            "end when neither is given."
        ),
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    **FILES_BODY["properties"],
                    "before": {"type": "string", "format": "uuid"},
                    "after": {"type": "string", "format": "uuid"},
                },
                "required": ["files"],
            }
        },
        responses={204: None},
    )
    def post(self, request, uuid):
        album = reachable_album(request.user, uuid)
        if album is None:
            return _not_found()
        if not _allowed("reorder", request.user, album):
            return Response(status=status.HTTP_403_FORBIDDEN)
        uuids = _parse_files(request.data)
        if isinstance(uuids, Response):
            return uuids
        anchors = {}
        for key in ("before", "after"):
            raw = request.data.get(key)
            if raw is None:
                continue
            anchors[key] = parse_uuid_or_none(raw)
            if anchors[key] is None:
                return _refused(f"Malformed UUID in {key}.")
        if len(anchors) > 1:
            return _refused("Give either before or after, not both.")
        try:
            move_items(album, uuids, **anchors)
        except ValueError:
            return _refused("Only photos of the album can be moved, next to another.")
        return Response(status=status.HTTP_204_NO_CONTENT)
