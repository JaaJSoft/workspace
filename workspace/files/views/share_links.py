"""Public API views for file share links (no authentication required)."""

from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.core import signing
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from workspace.common.http_ranges import serve_with_ranges
from workspace.common.pagination import OptInLimitOffsetPagination
from workspace.files.models import File, FileShareLink
from workspace.files.services.public_links import resolve_within

SIGNER = signing.TimestampSigner(salt="file-share-link")
ACCESS_TOKEN_MAX_AGE = 3600  # 1 hour


class ShareLinkVerifyThrottle(AnonRateThrottle):
    """5 attempts per minute per token for share link password verification."""

    rate = "5/min"

    def get_cache_key(self, request, view):
        token = view.kwargs.get("token", "")
        return self.cache_format % {
            "scope": self.scope,
            "ident": token,
        }


def _lookup_link(token):
    """The share link for *token*, or None. No policy, no status mapping."""
    return (
        FileShareLink.objects.select_related("file", "file__group", "created_by")
        .filter(token=token, file__deleted_at__isnull=True)
        .first()
    )


def _resolve_link(token):
    """Resolve a share link by token. Returns (link, error_response) tuple."""
    link = _lookup_link(token)
    if link is None:
        return None, Response(status=status.HTTP_404_NOT_FOUND)
    if link.is_expired:
        return None, Response(
            {"detail": "This share link has expired."},
            status=status.HTTP_410_GONE,
        )
    return link, None


def _check_password_access(link, request):
    """Check password access for a link. Returns error Response or None if OK."""
    if not link.has_password:
        return None
    access_token = request.query_params.get("access_token", "")
    if not access_token:
        return Response(
            {"detail": "Password required.", "has_password": True},
            status=status.HTTP_403_FORBIDDEN,
        )
    try:
        value = SIGNER.unsign(access_token, max_age=ACCESS_TOKEN_MAX_AGE)
        if value != link.token:
            raise signing.BadSignature
    except signing.BadSignature, signing.SignatureExpired:
        return Response(
            {"detail": "Invalid or expired access token."},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


def _record_access(link):
    """Increment view count and update last accessed time."""
    from django.db.models import F

    FileShareLink.objects.filter(pk=link.pk).update(
        view_count=F("view_count") + 1,
        last_accessed_at=timezone.now(),
    )


def _require_read(link):
    """Error Response when *link* does not grant read access, else None."""
    if not link.allows_read:
        return Response(status=status.HTTP_404_NOT_FOUND)
    return None


def _target_node(link, request, param):
    """Resolve the ``?<param>=<uuid>`` node, defaulting to the link's own root.

    Returns ``(node, error_response)``; exactly one of the two is None.
    """
    raw = request.query_params.get(param)
    if not raw:
        return link.file, None
    node = resolve_within(link, raw)
    if node is None:
        return None, Response(status=status.HTTP_404_NOT_FOUND)
    return node, None


def _entry_payload(node):
    """The public shape of one listed child. Deliberately not FileSerializer.

    FileSerializer exposes ``path``, which would name the folders above the
    share root to an anonymous visitor.
    """
    from workspace.files.ui.viewers import ViewerRegistry

    is_file = node.node_type == File.NodeType.FILE
    return {
        "uuid": str(node.uuid),
        "name": node.name,
        "node_type": node.node_type,
        "size": node.size,
        "type": node.type,
        "category": node.category,
        "mime_type": node.mime_type,
        "has_thumbnail": node.has_thumbnail,
        "updated_at": node.updated_at.isoformat() if node.updated_at else None,
        "is_viewable": bool(
            is_file and node.type and ViewerRegistry.is_supported(node.type, node.name)
        ),
    }


def _breadcrumbs(root, node):
    """Trail from the share root down to *node*, never above the root."""
    trail = []
    current = node
    while current is not None and current.pk != root.pk:
        trail.append({"uuid": str(current.uuid), "name": current.name})
        current = current.parent
    trail.append({"uuid": str(root.uuid), "name": root.name})
    trail.reverse()
    return trail


@extend_schema(tags=["Files - Shared Links"])
class SharedFileMetaView(APIView):
    """GET /api/v1/files/shared/{token} — public file metadata."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, token):
        link, err = _resolve_link(token)
        if err:
            return err

        payload = {
            "kind": "folder" if link.file.node_type == File.NodeType.FOLDER else "file",
            "name": link.file.name,
            "mode": link.mode,
            "allows_read": link.allows_read,
            "allows_upload": link.allows_upload,
            "has_password": link.has_password,
            "created_by_name": link.created_by.get_full_name()
            or link.created_by.username,
        }
        if link.allows_upload:
            payload["max_file_bytes"] = min(
                link.max_file_bytes or settings.FILES_DROP_MAX_FILE_BYTES,
                settings.FILES_DROP_MAX_FILE_BYTES,
            )
        # A write-only link says nothing about what is already inside.
        if not link.allows_read:
            return Response(payload)

        f = link.file
        if f.node_type == File.NodeType.FILE:
            from workspace.files.ui.viewers import ViewerRegistry

            payload.update(
                {
                    "mime_type": f.mime_type,
                    "size": f.size,
                    "category": f.category,
                    "is_viewable": ViewerRegistry.is_supported(f.type, f.name)
                    if f.type
                    else False,
                }
            )
        return Response(payload)


@extend_schema(tags=["Files - Shared Links"])
class SharedFileVerifyView(APIView):
    """POST /api/v1/files/shared/{token}/verify — verify password."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ShareLinkVerifyThrottle]

    def post(self, request, token):
        link, err = _resolve_link(token)
        if err:
            return err

        if not link.has_password:
            return Response(
                {"detail": "This link has no password."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_password = request.data.get("password", "")
        if not check_password(raw_password, link.password):
            return Response(
                {"detail": "Invalid password."},
                status=status.HTTP_403_FORBIDDEN,
            )

        access_token = SIGNER.sign(link.token)
        return Response({"access_token": access_token})


@extend_schema(tags=["Files - Shared Links"])
class SharedFileContentView(APIView):
    """GET /api/v1/files/shared/{token}/content — serve file inline."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, token):
        link, err = _resolve_link(token)
        if err:
            return err
        read_err = _require_read(link)
        if read_err:
            return read_err
        pwd_err = _check_password_access(link, request)
        if pwd_err:
            return pwd_err

        _record_access(link)

        f, err = _target_node(link, request, "file")
        if err:
            return err
        if f.node_type != File.NodeType.FILE:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if not f.content:
            return Response(
                {"detail": "File has no content."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if f.category in ("code", "text"):
            try:
                handle = f.content.open("rb")
                content = handle.read().decode("utf-8")
                handle.close()
                resp = HttpResponse(content, content_type=f.mime_type)
                resp["Content-Disposition"] = f'inline; filename="{f.name}"'
                return resp
            except UnicodeDecodeError:
                pass  # fall through to binary streaming
            except FileNotFoundError:
                return Response(status=status.HTTP_404_NOT_FOUND)

        # Binary files: stream with Range support so shared videos can seek.
        try:
            fh = f.content.open("rb")
        except FileNotFoundError:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return serve_with_ranges(
            request,
            file_handle=fh,
            file_size=f.size or 0,
            content_type=f.mime_type or "application/octet-stream",
            inline_filename=f.name,
        )


@extend_schema(tags=["Files - Shared Links"])
class SharedFileDownloadView(APIView):
    """GET /api/v1/files/shared/{token}/download — download file."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, token):
        link, err = _resolve_link(token)
        if err:
            return err
        read_err = _require_read(link)
        if read_err:
            return read_err
        pwd_err = _check_password_access(link, request)
        if pwd_err:
            return pwd_err

        _record_access(link)

        f, err = _target_node(link, request, "file")
        if err:
            return err
        if f.node_type != File.NodeType.FILE:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if not f.content:
            return Response(
                {"detail": "File has no content."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            fh = f.content.open("rb")
        except FileNotFoundError:
            return Response(status=status.HTTP_404_NOT_FOUND)
        # Range support lets the user resume an interrupted download.
        return serve_with_ranges(
            request,
            file_handle=fh,
            file_size=f.size or 0,
            content_type=f.mime_type or "application/octet-stream",
            attachment_filename=f.name,
        )


@extend_schema(tags=["Files - Shared Links"])
class SharedFolderEntriesView(APIView):
    """GET /api/v1/files/shared/{token}/entries - public folder listing."""

    permission_classes = [AllowAny]
    authentication_classes = []
    pagination_class = OptInLimitOffsetPagination

    def get(self, request, token):
        link, err = _resolve_link(token)
        if err:
            return err
        read_err = _require_read(link)
        if read_err:
            return read_err
        pwd_err = _check_password_access(link, request)
        if pwd_err:
            return pwd_err

        folder, err = _target_node(link, request, "folder")
        if err:
            return err
        if folder.node_type != File.NodeType.FOLDER:
            return Response(status=status.HTTP_404_NOT_FOUND)

        _record_access(link)

        children = File.objects.filter(
            parent=folder, deleted_at__isnull=True
        ).name_ordered("-node_type")

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(children, request, view=self)
        rows = children if page is None else page
        body = {
            "breadcrumbs": _breadcrumbs(link.file, folder),
            "entries": [_entry_payload(node) for node in rows],
        }
        if page is None:
            return Response(body)
        return paginator.get_paginated_response(body)


@extend_schema(tags=["Files - Shared Links"])
class SharedFileThumbnailView(APIView):
    """GET /api/v1/files/shared/{token}/thumbnail - public WebP thumbnail."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, token):
        from django.core.files.storage import default_storage
        from django.http import FileResponse

        from workspace.files.services.thumbnails.generation import get_thumbnail_path

        link, err = _resolve_link(token)
        if err:
            return err
        read_err = _require_read(link)
        if read_err:
            return read_err
        pwd_err = _check_password_access(link, request)
        if pwd_err:
            return pwd_err

        node, err = _target_node(link, request, "file")
        if err:
            return err
        if node.node_type != File.NodeType.FILE or not node.has_thumbnail:
            return Response(status=status.HTTP_404_NOT_FOUND)

        _record_access(link)

        thumb_path = get_thumbnail_path(node.uuid)
        if not default_storage.exists(thumb_path):
            return Response(status=status.HTTP_404_NOT_FOUND)

        response = FileResponse(
            default_storage.open(thumb_path, "rb"), content_type="image/webp"
        )
        # `public` here, unlike the authenticated endpoint: the token is the
        # only credential, and it is already in the URL a cache would key on.
        response["Cache-Control"] = "public, max-age=3600"
        return response
