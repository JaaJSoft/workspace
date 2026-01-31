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
        """Resolve path by walking parent->child one segment at a time."""
        parent = None
        node = None
        for part in parts:
            try:
                node = File.objects.get(
                    owner=user,
                    parent=parent,
                    name=part,
                    deleted_at__isnull=True,
                )
            except File.DoesNotExist:
                return None
            parent = node
        return node

    @staticmethod
    def _get_user(environ):
        return environ.get("workspace.user")
