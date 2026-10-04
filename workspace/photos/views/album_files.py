"""Album-scoped file endpoints: an album's photos, served through the album.

A member may see items they cannot open in Files (``queries.album_files``),
so these endpoints serve them after checking the file is an item of that
album the caller sees: a file outside it is a 404 whatever the caller can
open elsewhere. ``FileService`` and the Files endpoints are never widened.

The helpers at the top are shared with the public link endpoints
(``album_links``), which check a token instead of a membership.
"""

from django.core.files.storage import default_storage
from django.db.models import F
from django.http import FileResponse, StreamingHttpResponse
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.http_ranges import safe_filename, serve_with_ranges
from workspace.common.uuids import (
    BatchTooLarge,
    MalformedUuid,
    UuidBatchError,
    parse_uuid_batch,
)
from workspace.files.services.thumbnails.generation import get_thumbnail_path

from ..actions import AlbumActionRegistry
from ..queries import (
    album_files,
    direct_share_album_ids,
    get_album_role,
    reachable_album,
)

# One request's worth of a selection; the whole album has no limit.
MAX_FILES = 2000


def thumbnail_response(file_obj, *, cache_control):
    """*file_obj*'s WebP thumbnail, or a 404 when it has none."""
    path = get_thumbnail_path(file_obj.uuid)
    if not file_obj.has_thumbnail or not default_storage.exists(path):
        return Response(status=status.HTTP_404_NOT_FOUND)
    response = FileResponse(default_storage.open(path, "rb"), content_type="image/webp")
    response["Cache-Control"] = cache_control
    return response


def content_response(request, file_obj, *, attachment=False):
    """*file_obj*'s bytes, inline or as a download, with Range support so a
    video can seek and a download resume."""
    if not file_obj.content:
        return Response(status=status.HTTP_404_NOT_FOUND)
    try:
        handle = file_obj.content.open("rb")
    except OSError:
        return Response(status=status.HTTP_404_NOT_FOUND)
    name = {"attachment_filename" if attachment else "inline_filename": file_obj.name}
    return serve_with_ranges(
        request,
        file_handle=handle,
        file_size=file_obj.size or 0,
        content_type=file_obj.mime_type or "application/octet-stream",
        cache_control="private, no-cache",
        **name,
    )


def _arcnames(files):
    """``(file, name)`` pairs naming each file once in an archive: two photos
    called IMG_0001.jpg from two contributors become IMG_0001.jpg and
    IMG_0001 (2).jpg."""
    taken = set()
    for file_obj in files:
        stem, dot, ext = file_obj.name.rpartition(".")
        if not dot:
            stem, ext = file_obj.name, ""
        name, n = file_obj.name, 1
        while name.lower() in taken:
            n += 1
            name = f"{stem} ({n}).{ext}" if dot else f"{stem} ({n})"
        taken.add(name.lower())
        yield file_obj, name


def archive_response(files, title):
    """A zip of *files* streamed as it is built, named after *title*.

    Ordered as the album reads by capture date; a file whose blob is gone is
    left out rather than failing the whole archive.
    """
    from workspace.files.viewsets.content import _build_zip_stream

    ordered = (
        files.exclude(content="")
        .exclude(content__isnull=True)
        .order_by(F("media_item__taken_at").asc(nulls_last=True), "created_at", "uuid")
        .only("uuid", "name", "content", "size")
    )
    stream = _build_zip_stream(_arcnames(ordered.iterator()))
    response = StreamingHttpResponse(stream, content_type="application/zip")
    response["Content-Disposition"] = (
        f'attachment; filename="{safe_filename(title or "album")}.zip"'
    )
    return response


def _album_and_file(request, uuid, file_uuid):
    """The album and its item *file_uuid* as the caller sees them, or None
    for either when out of reach."""
    album = reachable_album(request.user, uuid)
    if album is None:
        return None, None
    return album, album_files(request.user, album).filter(uuid=file_uuid).first()


def _may_download(user, album):
    return AlbumActionRegistry.is_action_available(
        "download",
        user,
        album,
        role=get_album_role(user, album),
        direct=bool(direct_share_album_ids(user, [album])),
    )


@extend_schema(tags=["Photos - Album files"])
class AlbumFileThumbnailView(APIView):
    @extend_schema(
        summary="Thumbnail of an album photo",
        responses={200: OpenApiResponse(description="WebP thumbnail.")},
    )
    def get(self, request, uuid, file_uuid):
        _, file_obj = _album_and_file(request, uuid, file_uuid)
        if file_obj is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return thumbnail_response(file_obj, cache_control="private, max-age=3600")


@extend_schema(tags=["Photos - Album files"])
class AlbumFileContentView(APIView):
    @extend_schema(
        summary="An album photo or video, inline",
        description="For the viewer: Range requests let a video seek.",
        responses={200: OpenApiResponse(description="The file's bytes.")},
    )
    def get(self, request, uuid, file_uuid):
        _, file_obj = _album_and_file(request, uuid, file_uuid)
        if file_obj is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return content_response(request, file_obj)


@extend_schema(tags=["Photos - Album files"])
class AlbumFileDownloadView(APIView):
    @extend_schema(
        summary="Download an album photo or video",
        description="Offered by the album's download action.",
        responses={200: OpenApiResponse(description="The original file.")},
    )
    def get(self, request, uuid, file_uuid):
        album, file_obj = _album_and_file(request, uuid, file_uuid)
        if file_obj is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if not _may_download(request.user, album):
            return Response(status=status.HTTP_403_FORBIDDEN)
        return content_response(request, file_obj, attachment=True)


@extend_schema(tags=["Photos - Album files"])
class AlbumDownloadView(APIView):
    @extend_schema(
        summary="Download an album as a zip",
        description="Every photo and video the caller sees in the album, streamed.",
        responses={200: OpenApiResponse(description="A zip archive.")},
    )
    def get(self, request, uuid):
        album = reachable_album(request.user, uuid)
        if album is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if not _may_download(request.user, album):
            return Response(status=status.HTTP_403_FORBIDDEN)
        return archive_response(album_files(request.user, album), album.title)

    @extend_schema(
        summary="Download photos of an album as a zip",
        description="`files`: photos and videos of the album, as the caller sees it.",
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {"type": "string", "format": "uuid"},
                    }
                },
                "required": ["files"],
            }
        },
        responses={200: OpenApiResponse(description="A zip archive.")},
    )
    def post(self, request, uuid):
        album = reachable_album(request.user, uuid)
        if album is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if not _may_download(request.user, album):
            return Response(status=status.HTTP_403_FORBIDDEN)
        try:
            uuids = set(
                parse_uuid_batch(request.data, key="files", max_items=MAX_FILES)
            )
        except BatchTooLarge:
            detail = f"Too many files (max {MAX_FILES})."
        except MalformedUuid:
            detail = "Malformed UUID in files."
        except UuidBatchError:
            detail = "files must be a non-empty list."
        else:
            files = album_files(request.user, album).filter(uuid__in=uuids)
            if files.count() != len(uuids):
                return Response(status=status.HTTP_404_NOT_FOUND)
            return archive_response(files, album.title)
        return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)
