"""Content + thumbnail + download actions for FileViewSet."""

from django.db.models import Q
from django.http import Http404
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from workspace.files.metrics import FILES_DOWNLOAD_BYTES
from workspace.files.models import File
from workspace.files.services import FileService


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
        from django.http import FileResponse, HttpResponse

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
                response['Content-Disposition'] = f'inline; filename="{file_obj.name}"'
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
            content_type=file_obj.mime_type or 'application/octet-stream',
            as_attachment=False
        )
        response['Content-Disposition'] = f'inline; filename="{file_obj.name}"'
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

        file_handle = default_storage.open(thumb_path, 'rb')
        response = FileResponse(file_handle, content_type='image/webp')
        response['Cache-Control'] = 'public, max-age=86400'
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
        import io
        import zipfile

        from django.http import FileResponse, HttpResponse

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

        # Folder download as ZIP
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

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for desc in descendants:
                # Relative path inside the ZIP: strip the folder's own path prefix
                rel_path = desc.path[len(prefix):]
                try:
                    data = desc.content.read()
                    desc.content.close()
                    zf.writestr(rel_path, data)
                except Exception:
                    continue
        buf.seek(0)

        zip_name = f"{file_obj.name}.zip"
        zip_bytes = buf.getvalue()
        response = HttpResponse(zip_bytes, content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="{zip_name}"'
        FILES_DOWNLOAD_BYTES.inc(len(zip_bytes))
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
        import io
        import zipfile

        from django.http import HttpResponse

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

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for obj in file_objects:
                if obj.node_type == File.NodeType.FILE:
                    if not obj.content:
                        continue
                    try:
                        data = obj.content.read()
                        obj.content.close()
                        zf.writestr(obj.name, data)
                    except Exception:
                        continue
                else:
                    # Folder: add all descendant files
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
                        rel_path = f"{obj.name}/{desc.path[len(prefix):]}"
                        try:
                            data = desc.content.read()
                            desc.content.close()
                            zf.writestr(rel_path, data)
                        except Exception:
                            continue
        buf.seek(0)

        response = HttpResponse(buf.read(), content_type='application/zip')
        response['Content-Disposition'] = 'attachment; filename="download.zip"'
        return response
