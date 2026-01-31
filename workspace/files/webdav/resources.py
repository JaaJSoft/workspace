"""DAV resource classes wrapping the File model."""

import io
from tempfile import SpooledTemporaryFile

from django.core.files.base import ContentFile
from wsgidav.dav_provider import DAVCollection, DAVNonCollection

from workspace.files.models import File
from workspace.files.services import FileService


class _WriteBuffer:
    """Wraps a SpooledTemporaryFile so ``close()`` is deferred.

    WsgiDAV calls ``fileobj.close()`` before ``end_write()``, but we still
    need to read the data back.  This wrapper turns ``close()`` into a no-op
    and exposes ``real_close()`` for cleanup.
    """

    def __init__(self, max_size=2 * 1024 * 1024):
        self._buf = SpooledTemporaryFile(max_size=max_size)

    def write(self, data):
        return self._buf.write(data)

    def writelines(self, lines):
        return self._buf.writelines(lines)

    def close(self):
        pass  # deferred

    def read_all_and_close(self):
        self._buf.seek(0)
        data = self._buf.read()
        self._buf.close()
        return data

    def real_close(self):
        self._buf.close()


class RootCollection(DAVCollection):
    """Virtual root representing the user's top-level files/folders."""

    def __init__(self, path, environ):
        super().__init__(path, environ)
        self._user = environ["workspace.user"]

    def get_display_info(self):
        return {"type": "Directory"}

    def get_member_names(self):
        return list(
            File.objects.filter(
                owner=self._user,
                parent__isnull=True,
                deleted_at__isnull=True,
            ).values_list("name", flat=True)
        )

    def get_member(self, name):
        try:
            file_obj = File.objects.get(
                owner=self._user,
                parent__isnull=True,
                name=name,
                deleted_at__isnull=True,
            )
        except File.DoesNotExist:
            return None
        child_path = self.path.rstrip("/") + "/" + name
        if file_obj.is_folder():
            return FolderResource(child_path, self.environ, file_obj)
        return FileResource(child_path, self.environ, file_obj)

    def create_empty_resource(self, name):
        file_obj = FileService.create_file(self._user, name, parent=None)
        child_path = self.path.rstrip("/") + "/" + name
        return FileResource(child_path, self.environ, file_obj)

    def create_collection(self, name):
        FileService.create_folder(self._user, name, parent=None)
        return True


class FolderResource(DAVCollection):
    """Wraps a ``File(node_type=FOLDER)`` instance."""

    def __init__(self, path, environ, file_obj):
        super().__init__(path, environ)
        self._file = file_obj
        self._user = environ["workspace.user"]

    def get_display_info(self):
        return {"type": "Directory"}

    def get_creation_date(self):
        return self._file.created_at.timestamp()

    def get_last_modified(self):
        return self._file.updated_at.timestamp()

    def get_member_names(self):
        return list(
            File.objects.filter(
                parent=self._file,
                deleted_at__isnull=True,
            ).values_list("name", flat=True)
        )

    def get_member(self, name):
        try:
            child = File.objects.get(
                parent=self._file,
                name=name,
                deleted_at__isnull=True,
            )
        except File.DoesNotExist:
            return None
        child_path = self.path.rstrip("/") + "/" + name
        if child.is_folder():
            return FolderResource(child_path, self.environ, child)
        return FileResource(child_path, self.environ, child)

    def create_empty_resource(self, name):
        file_obj = FileService.create_file(
            self._user, name, parent=self._file
        )
        child_path = self.path.rstrip("/") + "/" + name
        return FileResource(child_path, self.environ, file_obj)

    def create_collection(self, name):
        FileService.create_folder(self._user, name, parent=self._file)
        return True

    def delete(self):
        self._file.soft_delete()

    def copy_move_single(self, dest_path, *, is_move):
        dest_parts = dest_path.strip("/").split("/")
        new_name = dest_parts[-1]
        dest_parent = _resolve_parent(self._user, dest_parts[:-1])
        _copy_as(self._file, dest_parent, self._user, new_name)

    def support_recursive_move(self, dest_path):
        return True

    def move_recursive(self, dest_path):
        dest_parts = dest_path.strip("/").split("/")
        new_name = dest_parts[-1]
        dest_parent = _resolve_parent(self._user, dest_parts[:-1])
        self._file.name = new_name
        self._file.parent = dest_parent
        self._file.save()

    def support_recursive_delete(self):
        return True


class FileResource(DAVNonCollection):
    """Wraps a ``File(node_type=FILE)`` instance."""

    def __init__(self, path, environ, file_obj):
        super().__init__(path, environ)
        self._file = file_obj
        self._user = environ["workspace.user"]

    def get_content_length(self):
        return self._file.size or 0

    def get_content_type(self):
        return self._file.mime_type or "application/octet-stream"

    def get_creation_date(self):
        return self._file.created_at.timestamp()

    def get_last_modified(self):
        return self._file.updated_at.timestamp()

    def get_display_info(self):
        return {"type": self._file.mime_type or "File"}

    def get_content(self):
        if not self._file.content:
            return io.BytesIO(b"")
        self._file.content.open("rb")
        return self._file.content

    def begin_write(self, content_type=None):
        self._write_buf = _WriteBuffer()
        return self._write_buf

    def end_write(self, *, with_errors):
        buf = self._write_buf
        if with_errors:
            buf.real_close()
            if self._file.size is None:
                self._file.delete(hard=True)
            return

        data = buf.read_all_and_close()
        content = ContentFile(data, name=self._file.name)
        FileService.update_content(self._file, content)

    def delete(self):
        if getattr(self, "_moved", False):
            return  # Already moved in copy_move_single; nothing to delete.
        self._file.soft_delete()

    def copy_move_single(self, dest_path, *, is_move):
        dest_parts = dest_path.strip("/").split("/")
        new_name = dest_parts[-1]
        dest_parent = _resolve_parent(self._user, dest_parts[:-1])

        if is_move:
            self._file.name = new_name
            self._file.parent = dest_parent
            self._file.save()
            # Prevent the post-move delete() from soft-deleting the moved file.
            self._moved = True
        else:
            _copy_as(self._file, dest_parent, self._user, new_name)

    def support_content_length(self):
        return True

    def support_recursive_move(self, dest_path):
        return False

    def support_etag(self):
        return True

    def get_etag(self):
        return f"{self._file.uuid}-{self._file.updated_at.timestamp()}"


def _copy_as(file_obj, dest_parent, owner, new_name):
    """Copy a File to *dest_parent* with a specific *new_name*.

    Unlike ``FileService.copy`` this does not auto-generate "(Copy)" suffixes.
    For folders, children are copied recursively with their original names.
    """
    if file_obj.is_folder():
        folder = FileService.create_folder(owner, new_name, parent=dest_parent)
        for child in File.objects.filter(parent=file_obj, deleted_at__isnull=True):
            _copy_as(child, folder, owner, child.name)
        return folder

    content = None
    if file_obj.content:
        try:
            file_obj.content.open("rb")
            data = file_obj.content.read()
        finally:
            file_obj.content.close()
        content = ContentFile(data, name=new_name)

    return FileService.create_file(
        owner, new_name, parent=dest_parent,
        content=content, mime_type=file_obj.mime_type,
    )


def _resolve_parent(user, path_parts):
    """Walk path segments to find the parent File (folder), or None for root."""
    parent = None
    for part in path_parts:
        try:
            parent = File.objects.get(
                owner=user,
                parent=parent,
                name=part,
                node_type=File.NodeType.FOLDER,
                deleted_at__isnull=True,
            )
        except File.DoesNotExist:
            return None
    return parent
