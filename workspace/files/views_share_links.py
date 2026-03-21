"""Public API views for file share links (no authentication required)."""

from django.core import signing
from django.contrib.auth.hashers import check_password
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from workspace.files.models import FileShareLink
from workspace.files.utils import FileTypeDetector


SIGNER = signing.TimestampSigner(salt='file-share-link')
ACCESS_TOKEN_MAX_AGE = 3600  # 1 hour


class ShareLinkVerifyThrottle(AnonRateThrottle):
    """5 attempts per minute per token for share link password verification."""
    rate = '5/min'

    def get_cache_key(self, request, view):
        token = view.kwargs.get('token', '')
        return self.cache_format % {
            'scope': self.scope,
            'ident': token,
        }


def _resolve_link(token):
    """Resolve a share link by token. Returns (link, error_response) tuple."""
    link = (
        FileShareLink.objects
        .select_related('file', 'created_by')
        .filter(token=token, file__deleted_at__isnull=True)
        .first()
    )
    if link is None:
        return None, Response(status=status.HTTP_404_NOT_FOUND)
    if link.is_expired:
        return None, Response(
            {'detail': 'This share link has expired.'},
            status=status.HTTP_410_GONE,
        )
    return link, None


def _check_password_access(link, request):
    """Check password access for a link. Returns error Response or None if OK."""
    if not link.has_password:
        return None
    access_token = request.query_params.get('access_token', '')
    if not access_token:
        return Response(
            {'detail': 'Password required.', 'has_password': True},
            status=status.HTTP_403_FORBIDDEN,
        )
    try:
        value = SIGNER.unsign(access_token, max_age=ACCESS_TOKEN_MAX_AGE)
        if value != link.token:
            raise signing.BadSignature
    except (signing.BadSignature, signing.SignatureExpired):
        return Response(
            {'detail': 'Invalid or expired access token.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


def _record_access(link):
    """Increment view count and update last accessed time."""
    from django.db.models import F
    FileShareLink.objects.filter(pk=link.pk).update(
        view_count=F('view_count') + 1,
        last_accessed_at=timezone.now(),
    )


class SharedFileMetaView(APIView):
    """GET /api/v1/files/shared/{token} — public file metadata."""
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, token):
        link, err = _resolve_link(token)
        if err:
            return err

        f = link.file
        from workspace.files.ui.viewers import ViewerRegistry
        return Response({
            'name': f.name,
            'mime_type': f.mime_type,
            'size': f.size,
            'category': FileTypeDetector.categorize(f.mime_type or '').value,
            'is_viewable': ViewerRegistry.is_supported(f.mime_type) if f.mime_type else False,
            'has_password': link.has_password,
            'created_by_name': link.created_by.get_full_name() or link.created_by.username,
        })


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
                {'detail': 'This link has no password.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_password = request.data.get('password', '')
        if not check_password(raw_password, link.password):
            return Response(
                {'detail': 'Invalid password.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        access_token = SIGNER.sign(link.token)
        return Response({'access_token': access_token})


class SharedFileContentView(APIView):
    """GET /api/v1/files/shared/{token}/content — serve file inline."""
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, token):
        link, err = _resolve_link(token)
        if err:
            return err
        pwd_err = _check_password_access(link, request)
        if pwd_err:
            return pwd_err

        _record_access(link)

        f = link.file
        if not f.content:
            return Response(
                {'detail': 'File has no content.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Text files: return decoded content, fall back to binary on decode error
        if f.mime_type and f.mime_type.startswith('text/'):
            try:
                handle = f.content.open('rb')
                content = handle.read().decode('utf-8')
                handle.close()
                resp = HttpResponse(content, content_type=f.mime_type)
                resp['Content-Disposition'] = f'inline; filename="{f.name}"'
                return resp
            except UnicodeDecodeError:
                pass  # fall through to binary streaming
            except FileNotFoundError:
                return Response(status=status.HTTP_404_NOT_FOUND)

        # Binary files: stream
        try:
            response = FileResponse(
                f.content.open('rb'),
                content_type=f.mime_type or 'application/octet-stream',
            )
            response['Content-Disposition'] = f'inline; filename="{f.name}"'
            return response
        except FileNotFoundError:
            return Response(status=status.HTTP_404_NOT_FOUND)


class SharedFileDownloadView(APIView):
    """GET /api/v1/files/shared/{token}/download — download file."""
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, token):
        link, err = _resolve_link(token)
        if err:
            return err
        pwd_err = _check_password_access(link, request)
        if pwd_err:
            return pwd_err

        _record_access(link)

        f = link.file
        if not f.content:
            return Response(
                {'detail': 'File has no content.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            response = FileResponse(
                f.content.open('rb'),
                content_type=f.mime_type or 'application/octet-stream',
                as_attachment=True,
                filename=f.name,
            )
            return response
        except FileNotFoundError:
            return Response(status=status.HTTP_404_NOT_FOUND)
