"""Public API views for file share links (no authentication required)."""

from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.core.files.storage import default_storage
from django.db.models import F
from django.http import FileResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from workspace.common.http_ranges import serve_with_ranges
from workspace.common.link_access import csrf_failure, keep_private
from workspace.files.models import File, FileEvent, FileShareLink
from workspace.files.services import tree
from workspace.files.services.downloads import signed_redirect
from workspace.files.services.events import record_event
from workspace.files.services.names import available_file_name
from workspace.files.services.public_links import (
    access_token_opens,
    grant_access,
    presented_access_token,
    resolve_within,
    sanitize_upload_name,
    schedule_upload_notification,
)
from workspace.files.services.scanning.policy import blocked_reason
from workspace.files.services.thumbnails.generation import (
    parse_thumbnail_size,
    thumbnail_variant_path,
)
from workspace.files.sse_provider import push_file_event
from workspace.files.ui.viewers import ViewerRegistry


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
            {"detail": _("This share link has expired.")},
            status=status.HTTP_410_GONE,
        )
    return link, None


def _check_password_access(link, request):
    """Error Response unless the visitor unlocked *link*'s password, else None."""
    if not link.has_password:
        return None
    access_token = presented_access_token(request, link)
    if not access_token:
        return Response(
            {"detail": _("Password required."), "has_password": True},
            status=status.HTTP_403_FORBIDDEN,
        )
    if not access_token_opens(link, access_token):
        return Response(
            {"detail": _("Invalid or expired access token.")},
            status=status.HTTP_403_FORBIDDEN,
        )
    request.unlocked_by_cookie = True
    return None


class _LinkReadView(APIView):
    """A read through a link: anonymous, and private once the cookie unlocked it."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        if getattr(request, "unlocked_by_cookie", False):
            keep_private(response)
        return response

    def readable_file(self, request, token, *, with_thumbnail=False):
        """The file ``?file=`` names in the link, as ``(file, error_response)``.

        Every gate - expiry, read mode, password, subtree, quarantine - runs
        before the access is counted.
        """
        link, err = _resolve_link(token)
        if err:
            return None, err
        read_err = _require_read(link)
        if read_err:
            return None, read_err
        pwd_err = _check_password_access(link, request)
        if pwd_err:
            return None, pwd_err

        f, err = _target_node(link, request, "file")
        if err:
            return None, err
        if f.node_type != File.NodeType.FILE or (
            with_thumbnail and not f.has_thumbnail
        ):
            return None, Response(status=status.HTTP_404_NOT_FOUND)

        quarantined = _check_quarantine(f)
        if quarantined is not None:
            return None, quarantined

        _record_access(link)
        return f, None


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
        {"detail": _("File is quarantined."), "reason": reason},
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
    """GET /api/v1/files/shared/{token} - public file metadata."""

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
    """POST /api/v1/files/shared/{token}/verify - verify password."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ShareLinkVerifyThrottle]

    def post(self, request, token):
        link, err = _resolve_link(token)
        if err:
            return err

        if not link.has_password:
            return Response(
                {"detail": _("This link has no password.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_password = request.data.get("password", "")
        if not check_password(raw_password, link.password):
            return Response(
                {"detail": _("Invalid password.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        response = Response(status=status.HTTP_204_NO_CONTENT)
        grant_access(response, request, link)
        return response


class _SharedFileBytesView(_LinkReadView):
    """The file's bytes, inline or as an attachment."""

    attachment = False

    def get(self, request, token):
        f, err = self.readable_file(request, token)
        if err:
            return err

        if not f.content:
            return Response(
                {"detail": _("File has no content.")},
                status=status.HTTP_404_NOT_FOUND,
            )

        redirect = signed_redirect(request, f, attachment=self.attachment)
        if redirect is not None:
            return redirect

        try:
            fh = f.content.open("rb")
        except FileNotFoundError:
            return Response(status=status.HTTP_404_NOT_FOUND)
        # Range support lets shared videos seek and interrupted downloads
        # resume, text included.
        disposition = "attachment_filename" if self.attachment else "inline_filename"
        return serve_with_ranges(
            request,
            file_handle=fh,
            file_size=f.size or 0,
            content_type=f.mime_type or "application/octet-stream",
            **{disposition: f.name},
        )


@extend_schema(tags=["Files - Shared Links"])
class SharedFileContentView(_SharedFileBytesView):
    """GET /api/v1/files/shared/{token}/content - serve file inline."""


@extend_schema(tags=["Files - Shared Links"])
class SharedFileDownloadView(_SharedFileBytesView):
    """GET /api/v1/files/shared/{token}/download - download file."""

    attachment = True


@extend_schema(tags=["Files - Shared Links"])
class SharedFileThumbnailView(_LinkReadView):
    """GET /api/v1/files/shared/{token}/thumbnail - public WebP thumbnail."""

    def get(self, request, token):
        size = parse_thumbnail_size(request.query_params.get("size"))
        if size is None:
            return Response(status=status.HTTP_400_BAD_REQUEST)
        node, err = self.readable_file(request, token, with_thumbnail=True)
        if err:
            return err

        thumb_path = thumbnail_variant_path(node.uuid, size)
        if thumb_path is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        response = FileResponse(
            default_storage.open(thumb_path, "rb"), content_type="image/webp"
        )
        # `public` here, unlike the authenticated endpoint: the link token is
        # the only credential, and it is in the URL a cache keys on. A link
        # behind a password turns it private on the way out (_LinkReadView).
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

        pwd_err = _check_password_access(link, request)
        if pwd_err:
            return pwd_err
        if link.has_password and (reason := csrf_failure(request)):
            return Response(
                {"detail": f"CSRF Failed: {reason}"}, status=status.HTTP_403_FORBIDDEN
            )

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
                {"detail": _("This file is too large for this link.")},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        target = self._resolve_target(link, request)
        if target is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if not self._reserve_slot(link):
            return Response(
                {"detail": _("This link is no longer accepting files.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            self._store(link, upload, request, target)
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
    def _resolve_target(link, request):
        """The folder the upload lands in: ``node`` on a link that shows the
        tree, the link's root otherwise. ``None`` is the uniform 404.

        A drop-only visitor never sees the tree, so ``node`` is ignored there
        rather than refused: a 204/404 split would tell a stranger which
        folder uuids exist under the root.
        """
        node_param = request.data.get("node")
        if not link.allows_read or not node_param:
            return link.file
        node = resolve_within(link, node_param)
        if node is None or node.node_type != File.NodeType.FOLDER:
            return None
        return node

    @staticmethod
    def _store(link, upload, request, target):
        root = link.file
        name = available_file_name(
            root.owner, target, sanitize_upload_name(upload.name)
        )
        # acting_user is the anonymous request user on purpose: record_event
        # normalises it to NULL, so the audit trail never claims the owner
        # uploaded what a stranger dropped.
        node = tree.create_file(
            root.owner,
            name,
            parent=target,
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
