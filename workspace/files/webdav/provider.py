"""DAV provider that maps URL paths to File model instances."""

import logging

from wsgidav.dav_provider import DAVProvider

from .resources import FileResource, FolderResource, RootCollection, resolve_path

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

        file_obj = resolve_path(user, path.strip("/").split("/"))
        if file_obj is None:
            return None

        if file_obj.is_folder():
            return FolderResource(path, environ, file_obj)
        return FileResource(path, environ, file_obj)

    @staticmethod
    def _get_user(environ):
        return environ.get("workspace.user")
