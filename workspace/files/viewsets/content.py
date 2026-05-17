"""Content + thumbnail + download actions for FileViewSet."""

from django.db.models import Q
from django.http import Http404
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from workspace.common.http_ranges import (
    safe_filename,
    parse_byte_range as _parse_byte_range,
    stream_range as _stream_range,
)
from workspace.files.metrics import FILES_DOWNLOAD_BYTES
from workspace.files.models import File
from workspace.files.services import FileService


def _chunked_field_file(field_file, block_size=65536):
    """Yield successive chunks from a Django FieldFile, then close it.

    Used as the data source for ZipStream so a multi-GB folder download
    can be streamed without ever holding more than one block in memory.
    """
    fh = field_file.open('rb')
    try:
        while True:
            chunk = fh.read(block_size)
            if not chunk:
                return
            yield chunk
    finally:
        fh.close()


def _build_zip_stream(entries):
    """Build a zipstream.ZipStream from an iterable of (File, arcname) pairs.

    Each entry's content is wired up as a lazy generator so the archive
    is generated as the response is iterated - constant RAM regardless of
    how much data the user requested. Entries with missing or vanished
    blobs are skipped silently (mirrors prior buffered behavior).
    """
    from zipstream import ZIP_DEFLATED, ZipStream

    zs = ZipStream(compress_type=ZIP_DEFLATED)
    for file_obj, arcname in entries:
        if not file_obj.content:
            continue
        zs.add(
            _chunked_field_file(file_obj.content),
            arcname=arcname,
            size=file_obj.size or None,
        )
    return zs


class ContentMixin:
    """Adds content, thumbnail, download, bulk_download actions."""

    @extend_schema(
        summary="Get file content",
        description="Serve file content with proper headers for inline viewing in browser.",
        responses={
            200: OpenApiResponse(
                description="File content with appropriate Content-Type and Content-Disposition headers.",
            ),
            400: OpenApiResponse(description="Not a file (folder)."),
            404: OpenApiResponse(description="File not found or no content."),
        },
    )
    @action(detail=True, methods=['get'], url_path='content')
    def content(self, request, uuid=None):
        """Serve file content with proper headers for inline viewing."""
        from django.http import FileResponse, HttpResponse, StreamingHttpResponse

        try:
            file_obj, perm = self._resolve_file_with_access(uuid)
        except Http404:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Only serve files, not folders
        if file_obj.node_type != File.NodeType.FILE:
            return Response({'detail': 'Not a file.'}, status=status.HTTP_400_BAD_REQUEST)

        # File must have content
        if not file_obj.content:
            return Response({'detail': 'No content.'}, status=status.HTTP_404_NOT_FOUND)

        content_type = file_obj.mime_type or 'application/octet-stream'

        # Range path: when the client requests a byte range (e.g. <video> seeking),
        # serve a 206 Partial Content. Bypasses the 304 short-circuit because the
        # client wants a specific slice, not a cache revalidation.
        range_header = request.META.get('HTTP_RANGE')
        if range_header:
            file_size = file_obj.size or file_obj.content.size
            parsed = _parse_byte_range(range_header, file_size)
            if parsed is None:
                resp = HttpResponse(status=416)
                resp['Content-Range'] = f'bytes */{file_size}'
                resp['Accept-Ranges'] = 'bytes'
                return resp
            start, end = parsed
            file_handle = file_obj.content.open('rb')
            response = StreamingHttpResponse(
                _stream_range(file_handle, start, end),
                status=206,
                content_type=content_type,
            )
            response['Content-Range'] = f'bytes {start}-{end}/{file_size}'
            response['Content-Length'] = str(end - start + 1)
            response['Accept-Ranges'] = 'bytes'
            response['Content-Disposition'] = f'inline; filename="{safe_filename(file_obj.name)}"'
            # Intentionally no ETag on 206: ConditionalGetMiddleware would
            # turn the 206 into a 304 whenever the client sends both Range
            # and If-None-Match (common from Chrome), starving the player.
            response['Cache-Control'] = 'private, no-cache'
            FILES_DOWNLOAD_BYTES.inc(end - start + 1)
            return response

        # Short-circuit: return 304 if ETag matches (avoids reading file from storage)
        not_modified = self._check_etag_304(request, file_obj)
        if not_modified:
            return not_modified

        # For text files, read and return directly (fixes streaming issues)
        if file_obj.mime_type and file_obj.mime_type.startswith('text/'):
            file_handle = None
            try:
                file_handle = file_obj.content.open('rb')
                content = file_handle.read().decode('utf-8')
                response = HttpResponse(content, content_type=file_obj.mime_type)
                response['Content-Disposition'] = f'inline; filename="{safe_filename(file_obj.name)}"'
                response['Accept-Ranges'] = 'bytes'
                self._set_file_cache_headers(response, file_obj)
                if file_obj.size:
                    FILES_DOWNLOAD_BYTES.inc(file_obj.size)
                return response
            except Exception:
                # Fallback to binary if UTF-8 fails
                pass
            finally:
                if file_handle:
                    file_handle.close()

        # For other files, use FileResponse with proper streaming
        # FileResponse will close the file handle when done
        file_handle = file_obj.content.open('rb')
        response = FileResponse(
            file_handle,
            content_type=content_type,
            as_attachment=False
        )
        response['Content-Disposition'] = f'inline; filename="{safe_filename(file_obj.name)}"'
        response['Accept-Ranges'] = 'bytes'
        self._set_file_cache_headers(response, file_obj)

        if file_obj.size:
            FILES_DOWNLOAD_BYTES.inc(file_obj.size)
        return response

    @extend_schema(
        summary="Get file thumbnail",
        description="Serve a pre-generated WebP thumbnail for image files.",
        responses={
            200: OpenApiResponse(description="WebP thumbnail image."),
            404: OpenApiResponse(description="No thumbnail available."),
        },
    )
    @action(detail=True, methods=['get'], url_path='thumbnail')
    def thumbnail(self, request, uuid=None):
        """Serve a pre-generated thumbnail for an image file."""
        from django.core.files.storage import default_storage
        from django.http import FileResponse

        from workspace.files.services.thumbnails import get_thumbnail_path

        try:
            file_obj, perm = self._resolve_file_with_access(uuid)
        except Http404:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        if file_obj.node_type != File.NodeType.FILE:
            return Response({'detail': 'Not a file.'}, status=status.HTTP_400_BAD_REQUEST)

        thumb_path = get_thumbnail_path(file_obj.uuid)
        if not default_storage.exists(thumb_path):
            return Response({'detail': 'No thumbnail.'}, status=status.HTTP_404_NOT_FOUND)

        # Thumbnails are content-addressed: the file's updated_at changes
        # whenever it gets regenerated, so a UUID+timestamp ETag is exact.
        etag = self._file_etag(file_obj)
        if_none_match = request.META.get('HTTP_IF_NONE_MATCH')
        if if_none_match and if_none_match.strip('"') == etag.strip('"'):
            from django.http import HttpResponse as DjHttpResponse
            resp = DjHttpResponse(status=304)
            resp['ETag'] = etag
            resp['Cache-Control'] = 'private, max-age=86400, stale-while-revalidate=604800'
            return resp

        file_handle = default_storage.open(thumb_path, 'rb')
        response = FileResponse(file_handle, content_type='image/webp')
        response['ETag'] = etag
        # 24 h hot cache, 7 d stale-while-revalidate. `private` so a shared
        # proxy can't leak one user's thumbnail to another. Set inline rather
        # than via CacheControlMixin: the viewset mixes CacheControlMixin in
        # at the class level for its JSON endpoints, and a per-action override
        # would need bespoke plumbing the mixin does not provide.
        response['Cache-Control'] = 'private, max-age=86400, stale-while-revalidate=604800'
        return response

    @extend_schema(
        summary="Download file or folder",
        description=(
            "Download a single file as an attachment, or an entire folder as a ZIP archive. "
            "For folders, all non-deleted descendant files with content are included."
        ),
        responses={
            200: OpenApiResponse(
                description="File content or ZIP archive.",
            ),
            404: OpenApiResponse(description="File not found or no content."),
        },
    )
    @action(detail=True, methods=['get'], url_path='download')
    def download(self, request, uuid=None):
        """Download a file or a folder as a ZIP archive."""
        from django.http import FileResponse, StreamingHttpResponse

        try:
            file_obj, perm = self._resolve_file_with_access(uuid)
        except Http404:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Short-circuit: return 304 if ETag matches (single files only)
        if file_obj.node_type == File.NodeType.FILE:
            not_modified = self._check_etag_304(request, file_obj)
            if not_modified:
                return not_modified

        # Single file download
        if file_obj.node_type == File.NodeType.FILE:
            if not file_obj.content:
                return Response({'detail': 'No content.'}, status=status.HTTP_404_NOT_FOUND)
            file_handle = file_obj.content.open('rb')
            response = FileResponse(
                file_handle,
                content_type=file_obj.mime_type or 'application/octet-stream',
                as_attachment=True,
                filename=file_obj.name,
            )
            self._set_file_cache_headers(response, file_obj)
            if file_obj.size:
                FILES_DOWNLOAD_BYTES.inc(file_obj.size)
            return response

        # Folder download as ZIP - streamed so RAM usage stays bounded
        # (the previous BytesIO-then-getvalue() held the full archive in
        # memory before the first byte hit the socket).
        folder_path = file_obj.path or file_obj.get_path()
        prefix = f"{folder_path}/"
        # Use the folder owner (not request.user) so a folder shared from
        # another user still expands its descendants. Group folders use the
        # group_id branch.
        desc_filter = Q(owner_id=file_obj.owner_id)
        if file_obj.group_id:
            desc_filter = Q(group_id=file_obj.group_id)
        descendants = File.objects.filter(
            desc_filter,
            node_type=File.NodeType.FILE,
            deleted_at__isnull=True,
            path__startswith=prefix,
        ).exclude(content='').exclude(content__isnull=True)

        zs = _build_zip_stream(
            (desc, desc.path[len(prefix):]) for desc in descendants
        )
        zip_name = f"{safe_filename(file_obj.name)}.zip"
        response = StreamingHttpResponse(zs, content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="{zip_name}"'
        return response

    @extend_schema(
        summary="Download multiple files/folders as ZIP",
        description=(
            "Download multiple files and folders as a single ZIP archive. "
            "Folders are included recursively with their contents."
        ),
        request={
            'application/json': {
                'type': 'object',
                'properties': {
                    'uuids': {
                        'type': 'array',
                        'items': {'type': 'string', 'format': 'uuid'},
                        'description': 'List of file/folder UUIDs to download',
                    },
                },
                'required': ['uuids'],
            },
        },
        responses={
            200: OpenApiResponse(description="ZIP archive."),
            400: OpenApiResponse(description="Invalid request."),
            404: OpenApiResponse(description="One or more UUIDs not found."),
        },
    )
    @action(detail=False, methods=['post'], url_path='bulk-download')
    def bulk_download(self, request):
        """Download multiple files/folders as a single ZIP archive."""
        from django.http import StreamingHttpResponse

        uuids = request.data.get('uuids', [])
        if not isinstance(uuids, list) or len(uuids) == 0:
            return Response(
                {'detail': 'uuids must be a non-empty list.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(uuids) > 200:
            return Response(
                {'detail': 'Too many UUIDs (max 200).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        file_objects = list(
            File.objects.filter(
                FileService.accessible_files_q(request.user),
                uuid__in=uuids,
                deleted_at__isnull=True,
            ).only(
                'uuid', 'name', 'path', 'content', 'node_type', 'owner_id',
            ).distinct()
        )

        if len(file_objects) != len(set(uuids)):
            return Response(
                {'detail': 'One or more UUIDs not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        def _entries():
            for obj in file_objects:
                if obj.node_type == File.NodeType.FILE:
                    if obj.content:
                        yield obj, obj.name
                else:
                    # Folder: add all descendant files under <folder name>/
                    folder_path = obj.path or obj.get_path()
                    prefix = f"{folder_path}/"
                    descendants = File.objects.filter(
                        owner=request.user,
                        node_type=File.NodeType.FILE,
                        deleted_at__isnull=True,
                        path__startswith=prefix,
                    ).only(
                        'uuid', 'name', 'path', 'content',
                    ).exclude(content='').exclude(content__isnull=True)
                    for desc in descendants:
                        yield desc, f"{obj.name}/{desc.path[len(prefix):]}"

        zs = _build_zip_stream(_entries())
        response = StreamingHttpResponse(zs, content_type='application/zip')
        response['Content-Disposition'] = 'attachment; filename="download.zip"'
        return response
