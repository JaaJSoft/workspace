"""Public endpoints of album links: no account, the token is the credential.

A token reaches the items of its album that the album shows through links
(``queries.link_files``), nothing else: any other file is a 404, whatever
uuid the request names. A password-protected link also wants the access
token ``verify`` hands out, in ``?access_token=``.
"""

from django.contrib.auth.hashers import check_password
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.core.module_guard import ModuleVisible
from workspace.files.views.share_links import ShareLinkVerifyThrottle

from ..queries import link_files
from ..services.album_links import access_token_for, find_link, has_access
from .album_files import (
    archive_response,
    content_response,
    rendition_response,
    thumbnail_response,
)


def _open_link(request, token):
    """``(link, None)`` when the request may read the link, else
    ``(None, error response)``."""
    link = find_link(token)
    if link is None:
        return None, Response(status=status.HTTP_404_NOT_FOUND)
    if link.is_expired:
        return None, Response(
            {"detail": "This link has expired."}, status=status.HTTP_410_GONE
        )
    if not has_access(link, request.query_params.get("access_token", "")):
        return None, Response(
            {"detail": "Password required.", "has_password": True},
            status=status.HTTP_403_FORBIDDEN,
        )
    return link, None


def _link_file(request, token, file_uuid):
    """``(link, file, None)`` for an item the link shows, else an error."""
    link, error = _open_link(request, token)
    if error:
        return None, None, error
    files = link_files(link.album).select_related("media_item")
    file_obj = files.filter(uuid=file_uuid).first()
    if file_obj is None:
        return None, None, Response(status=status.HTTP_404_NOT_FOUND)
    return link, file_obj, None


class _PublicView(APIView):
    # No authentication, so ModuleVisible lets every visitor through; it is
    # kept for the preview audience a signed-in caller still answers to.
    permission_classes = [ModuleVisible, AllowAny]
    authentication_classes = []


@extend_schema(tags=["Photos - Album links"])
class AlbumLinkVerifyView(_PublicView):
    throttle_classes = [ShareLinkVerifyThrottle]

    @extend_schema(
        summary="Unlock a password-protected album link",
        description="Answers an `access_token` to pass as `?access_token=`.",
        request={
            "application/json": {
                "type": "object",
                "properties": {"password": {"type": "string"}},
            }
        },
        responses={200: OpenApiResponse(description="The access token.")},
    )
    def post(self, request, token):
        link = find_link(token)
        if link is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if link.is_expired:
            return Response(
                {"detail": "This link has expired."}, status=status.HTTP_410_GONE
            )
        if not link.has_password:
            return Response(
                {"detail": "This link has no password."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = request.data if isinstance(request.data, dict) else {}
        if not check_password(str(data.get("password", "")), link.password):
            return Response(
                {"detail": "Invalid password."}, status=status.HTTP_403_FORBIDDEN
            )
        return Response({"access_token": access_token_for(link)})


@extend_schema(tags=["Photos - Album links"])
class AlbumLinkThumbnailView(_PublicView):
    @extend_schema(summary="Thumbnail of a photo of a linked album")
    def get(self, request, token, file_uuid):
        _, file_obj, error = _link_file(request, token, file_uuid)
        if error:
            return error
        # `public`: the token is the only credential and already in the URL
        # a cache would key on, as for file links.
        return thumbnail_response(file_obj, cache_control="public, max-age=3600")


@extend_schema(tags=["Photos - Album links"])
class AlbumLinkContentView(_PublicView):
    @extend_schema(
        summary="A photo or video of a linked album, inline",
        description=(
            "The original when the link allows downloads, otherwise a "
            "rendition without metadata."
        ),
    )
    def get(self, request, token, file_uuid):
        link, file_obj, error = _link_file(request, token, file_uuid)
        if error:
            return error
        if not link.allow_download:
            return rendition_response(request, file_obj)
        return content_response(request, file_obj)


@extend_schema(tags=["Photos - Album links"])
class AlbumLinkDownloadView(_PublicView):
    @extend_schema(summary="Download a photo or video of a linked album")
    def get(self, request, token, file_uuid):
        link, file_obj, error = _link_file(request, token, file_uuid)
        if error:
            return error
        if not link.allow_download:
            return Response(status=status.HTTP_403_FORBIDDEN)
        return content_response(request, file_obj, attachment=True)


@extend_schema(tags=["Photos - Album links"])
class AlbumLinkArchiveView(_PublicView):
    @extend_schema(summary="Download a linked album as a zip")
    def get(self, request, token):
        link, error = _open_link(request, token)
        if error:
            return error
        if not link.allow_download:
            return Response(status=status.HTTP_403_FORBIDDEN)
        return archive_response(link_files(link.album), link.album.title)
