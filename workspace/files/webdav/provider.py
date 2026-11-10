"""DAV provider that maps URL paths to File model instances."""

import logging

from wsgidav.dav_provider import DAVProvider

from workspace.files.models import name_forms
from workspace.files.services import FileService

from .resources import FileResource, FolderResource, RootCollection, dav_nodes

logger = logging.getLogger(__name__)


class WorkspaceDAVProvider(DAVProvider):
    """Resolve WebDAV paths to Django ``File`` objects."""

    def __init__(self):
        super().__init__()

    def get_resource_inst(self, path, environ):
        user = self._get_user(environ)
        if user is None:
            return None

        path = path.rstrip("/") or "/"

        if path == "/":
            return RootCollection("/", environ)

        parts = path.strip("/").split("/")

        # File.path stores the tree path without username prefix,
        # e.g. "FolderA/SubFolder/file.txt"
        # Use .filter().first() instead of .get() to survive duplicates
        # that can arise from concurrent PUT requests (race in
        # create_empty_resource).
        file_obj = dav_nodes(
            FileService.accessible_files_q(user),
            path__in=name_forms("/".join(parts)),
        ).first()

        if file_obj is None:
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
        accessible = FileService.accessible_files_q(user)

        if len(parts) == 1:
            return dav_nodes(
                accessible,
                parent__isnull=True,
                name__in=name_forms(parts[0]),
            ).first()

        parent_path = "/".join(parts[:-1])
        parent = dav_nodes(
            accessible,
            path__in=name_forms(parent_path),
        ).first()
        if parent is None:
            return None
        return dav_nodes(
            accessible,
            parent=parent,
            name__in=name_forms(parts[-1]),
        ).first()

    @staticmethod
    def _get_user(environ):
        return environ.get("workspace.user")
