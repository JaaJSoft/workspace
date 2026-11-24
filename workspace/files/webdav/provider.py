"""DAV provider that maps URL paths to File model instances."""

import logging
from urllib.parse import unquote

from wsgidav.dav_provider import DAVProvider

from . import namespace
from .resources import (
    FileResource,
    FolderResource,
    RootCollection,
    top_level_collection,
)

logger = logging.getLogger(__name__)


class WorkspaceDAVProvider(DAVProvider):
    """Resolve WebDAV paths to Django ``File`` objects.

    ``namespace.py`` says what a path names; this wraps the answer in the
    resource wsgidav works with.
    """

    def __init__(self):
        super().__init__()

    def get_resource_inst(self, path, environ):
        user = self._get_user(environ)
        # wsgidav asks for the parent of the root, which has no path.
        if user is None or path is None:
            return None

        path = path.rstrip("/") or "/"
        parts = path.strip("/").split("/")

        if path == "/":
            return RootCollection("/", environ)
        if len(parts) == 1:
            return top_level_collection(parts[0], path, environ)

        file_obj = namespace.node_at(user, parts)
        if file_obj is None:
            return None
        if file_obj.is_folder():
            return FolderResource(path, environ, file_obj)
        return FileResource(path, environ, file_obj)

    def ref_url_to_path(self, ref_url):
        """The path of the resource a lock is filed under: wsgidav hands a
        lock's key back to find the resource it was taken on."""
        return namespace.lock_key_path(unquote(ref_url))

    @staticmethod
    def _get_user(environ):
        return environ.get("workspace.user")
