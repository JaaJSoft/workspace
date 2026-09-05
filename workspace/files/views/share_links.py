"""Public API views for file share links (no authentication required)."""

from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.core import signing
from django.core.files.storage import default_storage
from django.db.models import F
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from workspace.common.http_ranges import serve_with_ranges
from workspace.files.models import File, FileEvent, FileShareLink
from workspace.files.services import FileService
from workspace.files.services.events import record_event
from workspace.files.services.public_links import (
    resolve_within,
    sanitize_upload_name,
    schedule_upload_notification,
)
from workspace.files.services.scanning.policy import blocked_reason
from workspace.files.services.thumbnails.generation import get_thumbnail_path
from workspace.files.sse_provider import push_file_event
from workspace.files.ui.viewers import ViewerRegistry

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


class ShareLinkUploadTokenThrottle(AnonRateThrottle):
    """Bound how fast one link can be filled, whoever is filling it."""

    scope = "share_link_upload_token"

    def get_rate(self):
        return settings.FILES_DROP_UPLOAD_RATE_TOKEN

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": view.kwargs.get("token", ""),
        }


class ShareLinkUploadIPThrottle(AnonRateThrottle):
    """Bound how fast one client can fill links, however many it holds."""

    scope = "share_link_upload_ip"

    def get_rate(self):
        return settings.FILES_DROP_UPLOAD_RATE_IP


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


def _verify_access_token(link, access_token):
    """Check *access_token* against *link*'s password. Error Response or None.

    Shared by the read path (token in the query string) and the write path
    (token in a header) - the token source is the one line that genuinely
    differs between the two callers.
    """
    if not link.has_password:
        return None
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


def _check_password_access(link, request):
    """Check password access for a link. Returns error Response or None if OK."""
    return _verify_access_token(link, request.query_params.get("access_token", ""))


def _check_quarantine(file_obj):
    """403 when the malware policy denies this file, else None.

    A public link hands bytes to an anonymous visitor, which is exactly the
    case the scanner exists for; naming the signature here is useful, not a
    disclosure problem.
    """
    reason = blocked_reason(file_obj)
    if reason is None:
        return None
    return Response(
        {"detail": "File is quarantined.", "reason": reason},
        status=status.HTTP_403_FORBIDDEN,
    )


def _record_access(link):
    """Increment view count and update last accessed time.

    Underscore-private by convention, not by enforcement: ``files.ui.views``
    also imports this directly for the page's own listing render, which
    needs the exact same increment the content/download/thumbnail endpoints
    below already do. Deliberately shared, not a leak.
    """
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

        f, err = _target_node(link, request, "file")
        if err:
            return err
        if f.node_type != File.NodeType.FILE:
            return Response(status=status.HTTP_404_NOT_FOUND)

        quarantined = _check_quarantine(f)
        if quarantined is not None:
            return quarantined

        _record_access(link)

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

        f, err = _target_node(link, request, "file")
        if err:
            return err
        if f.node_type != File.NodeType.FILE:
            return Response(status=status.HTTP_404_NOT_FOUND)

        quarantined = _check_quarantine(f)
        if quarantined is not None:
            return quarantined

        _record_access(link)

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
class SharedFileThumbnailView(APIView):
    """GET /api/v1/files/shared/{token}/thumbnail - public WebP thumbnail."""

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

        node, err = _target_node(link, request, "file")
        if err:
            return err
        if node.node_type != File.NodeType.FILE or not node.has_thumbnail:
            return Response(status=status.HTTP_404_NOT_FOUND)

        quarantined = _check_quarantine(node)
        if quarantined is not None:
            return quarantined

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


@extend_schema(tags=["Files - Shared Links"])
class SharedFolderUploadView(APIView):
    """POST /api/v1/files/shared/{token}/upload - anonymous write-only drop."""

    permission_classes = [AllowAny]
    authentication_classes = []
    parser_classes = [MultiPartParser]
    throttle_classes = [ShareLinkUploadTokenThrottle, ShareLinkUploadIPThrottle]

    def post(self, request, token):
        link = self._resolve_writable(token)
        if link is None:
            # One answer for "no such token", "expired" and "not a drop link":
            # the write path must not confirm that a token exists.
            return Response(status=status.HTTP_404_NOT_FOUND)

        pwd_err = self._check_password(link, request)
        if pwd_err:
            return pwd_err

        parts = request.FILES.getlist("file")
        if len(parts) != 1:
            return Response(
                {"detail": "Send exactly one file."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        upload = parts[0]

        ceiling = settings.FILES_DROP_MAX_FILE_BYTES
        max_bytes = min(link.max_file_bytes or ceiling, ceiling)
        if (upload.size or 0) > max_bytes:
            return Response(
                {"detail": "This file is too large for this link."},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        if not self._reserve_slot(link):
            return Response(
                {"detail": "This link is no longer accepting files."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            self._store(link, upload, request)
        except Exception:
            self._release_slot(link)
            raise

        schedule_upload_notification(link)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @staticmethod
    def _resolve_writable(token):
        """The link, or None for missing, expired and not-a-drop-link alike.

        Reuses `_lookup_link` rather than repeating its query: the read path's
        only difference is that it maps expiry to 410, and that mapping lives
        in `_resolve_link`, not in the lookup.
        """
        link = _lookup_link(token)
        if link is None or link.is_expired or not link.allows_upload:
            return None
        return link

    @staticmethod
    def _check_password(link, request):
        # The capability travels in a header, not the query string: this is
        # the one endpoint that writes, and a URL is copied into every access
        # log line, which redaction does not reach.
        return _verify_access_token(link, request.headers.get("X-Share-Access", ""))

    @staticmethod
    def _reserve_slot(link):
        """Claim one of the link's remaining slots. False when it is full.

        One statement, so two concurrent anonymous uploads cannot both win.
        The cap is resolved in Python because the effective limit is the lower
        of the owner's value and the live setting, and only one of the two is
        a column.
        """
        ceiling = settings.FILES_DROP_MAX_FILE_COUNT
        effective_cap = min(link.max_file_count or ceiling, ceiling)
        return bool(
            FileShareLink.objects.filter(
                pk=link.pk, upload_count__lt=effective_cap
            ).update(upload_count=F("upload_count") + 1)
        )

    @staticmethod
    def _release_slot(link):
        FileShareLink.objects.filter(pk=link.pk, upload_count__gt=0).update(
            upload_count=F("upload_count") - 1
        )

    @staticmethod
    def _store(link, upload, request):
        root = link.file
        name = FileService.available_file_name(
            root.owner, root, sanitize_upload_name(upload.name)
        )
        # acting_user is the anonymous request user on purpose: record_event
        # normalises it to NULL, so the audit trail never claims the owner
        # uploaded what a stranger dropped.
        node = FileService.create_file(
            root.owner,
            name,
            parent=root,
            content=upload,
            group=root.group,
            acting_user=request.user,
        )
        record_event(
            node,
            request.user,
            FileEvent.Action.LINK_UPLOAD,
            {"link_uuid": str(link.uuid)},
        )
        push_file_event(node, "file_created", None)
        return node
