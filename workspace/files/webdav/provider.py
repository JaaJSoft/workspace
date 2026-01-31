"""DAV provider that maps URL paths to File model instances."""

import logging

from django.db import close_old_connections
from wsgidav.dav_provider import DAVProvider

from workspace.files.models import File
from .resources import FileResource, FolderResource, RootCollection

logger = logging.getLogger(__name__)


class WorkspaceDAVProvider(DAVProvider):
    """Resolve WebDAV paths to Django ``File`` objects."""

    def __init__(self):
        super().__init__()

    def get_resource_inst(self, path, environ):
        close_old_connections()

        user = self._get_user(environ)
        if user is None:
            return None

        path = path.rstrip("/") or "/"

        if path == "/":
            return RootCollection("/", environ)

        parts = path.strip("/").split("/")

        # File.path stores the tree path without username prefix,
        # e.g. "FolderA/SubFolder/file.txt"
        try:
            file_obj = File.objects.get(
                owner=user,
                path="/".join(parts),
                deleted_at__isnull=True,
            )
        except File.DoesNotExist:
            # Fall back to walking the tree segment by segment
            file_obj = self._walk_path(user, parts)
            if file_obj is None:
                return None

        if file_obj.is_folder():
            return FolderResource(path, environ, file_obj)
        return FileResource(path, environ, file_obj)

    @staticmethod
    def _walk_path(user, parts):
        """Resolve path by name + parent as a single query fallback.

        Tries matching the leaf name with the expected parent path first
        (1 query).  Falls back to a segment-by-segment walk only when
        the parent path lookup also misses (e.g. stale ``path`` field).
        """
        if len(parts) == 1:
            return (
                File.objects.filter(
                    owner=user,
                    parent__isnull=True,
                    name=parts[0],
                    deleted_at__isnull=True,
                ).first()
            )

        parent_path = "/".join(parts[:-1])
        try:
            parent = File.objects.get(
                owner=user,
                path=parent_path,
                deleted_at__isnull=True,
            )
            return (
                File.objects.filter(
                    owner=user,
                    parent=parent,
                    name=parts[-1],
                    deleted_at__isnull=True,
                ).first()
            )
        except File.DoesNotExist:
            return None

    @staticmethod
    def _get_user(environ):
        return environ.get("workspace.user")
