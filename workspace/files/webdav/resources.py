"""DAV resource classes wrapping the File model."""

import io
import logging
import time
from tempfile import SpooledTemporaryFile

from django.conf import settings as django_settings
from django.core.files.base import File as DjangoFile
from django.db import transaction
from wsgidav.dav_provider import DAVCollection, DAVNonCollection

from workspace.files.models import File
from workspace.files.services import FileService

logger = logging.getLogger(__name__)


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

    def as_django_file(self, name):
        """Return a Django ``File`` wrapping the buffer for streaming to storage.

        The caller is responsible for calling ``real_close()`` afterwards.
        """
        self._buf.seek(0, 2)
        size = self._buf.tell()
        self._buf.seek(0)
        f = DjangoFile(self._buf, name=name)
        f.size = size
        return f

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
        self._prefetch_members()
        return [f.name for f in self._members_cache]

    def get_member(self, name):
        self._prefetch_members()
        for f in self._members_cache:
            if f.name == name:
                return self._wrap(f)
        return None

    def get_member_list(self):
        self._prefetch_members()
        return [self._wrap(f) for f in self._members_cache]

    def _prefetch_members(self):
        if hasattr(self, "_members_cache"):
            return
        self._members_cache = list(
            File.objects.filter(
                owner=self._user,
                parent__isnull=True,
                deleted_at__isnull=True,
            )
        )

    def _wrap(self, file_obj):
        child_path = self.path.rstrip("/") + "/" + file_obj.name
        if file_obj.is_folder():
            return FolderResource(child_path, self.environ, file_obj)
        return FileResource(child_path, self.environ, file_obj)

    def create_empty_resource(self, name):
        # Reuse an existing file to avoid duplicates from concurrent PUTs
        # (e.g. Windows retries while a slow upload is still in progress).
        file_obj = File.objects.filter(
            owner=self._user, name=name, parent__isnull=True,
            node_type=File.NodeType.FILE, deleted_at__isnull=True,
        ).first()
        if file_obj is None:
            file_obj = FileService.create_file(self._user, name, parent=None)
        child_path = self.path.rstrip("/") + "/" + name
        return FileResource(child_path, self.environ, file_obj)

    def create_collection(self, name):
        FileService.create_folder(self._user, name, parent=None)
        return True

    def get_used_bytes(self):
        return FileService.storage_used(self._user)

    def get_available_bytes(self):
        return max(0, django_settings.STORAGE_QUOTA_BYTES - self.get_used_bytes())


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
        self._prefetch_members()
        return [f.name for f in self._members_cache]

    def get_member(self, name):
        self._prefetch_members()
        for f in self._members_cache:
            if f.name == name:
                return self._wrap(f)
        return None

    def get_member_list(self):
        self._prefetch_members()
        return [self._wrap(f) for f in self._members_cache]

    def _prefetch_members(self):
        if hasattr(self, "_members_cache"):
            return
        self._members_cache = list(
            File.objects.filter(
                parent=self._file,
                deleted_at__isnull=True,
            )
        )

    def _wrap(self, file_obj):
        child_path = self.path.rstrip("/") + "/" + file_obj.name
        if file_obj.is_folder():
            return FolderResource(child_path, self.environ, file_obj)
        return FileResource(child_path, self.environ, file_obj)

    def create_empty_resource(self, name):
        # Reuse an existing file to avoid duplicates from concurrent PUTs.
        file_obj = File.objects.filter(
            owner=self._user, name=name, parent=self._file,
            node_type=File.NodeType.FILE, deleted_at__isnull=True,
        ).first()
        if file_obj is None:
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

        if new_name != self._file.name:
            FileService.rename(self._file, new_name)

        if dest_parent != self._file.parent:
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
        self._write_started_at = time.monotonic()
        logger.info(
            "webdav PUT begin: user=%s path=%s",
            getattr(self._user, "username", "?"),
            self.path,
        )
        return self._write_buf

    def end_write(self, *, with_errors):
        buf = self._write_buf
        elapsed = time.monotonic() - getattr(self, "_write_started_at", time.monotonic())
        if with_errors:
            buf.real_close()
            logger.warning(
                "webdav PUT failed: user=%s path=%s elapsed=%.2fs",
                getattr(self._user, "username", "?"),
                self.path,
                elapsed,
            )
            if self._file.size is None:
                self._file.delete(hard=True)
            return

        try:
            content = buf.as_django_file(self._file.name)
            size = content.size
            # Serialize concurrent writes to the same file (e.g. Windows
            # retries while the first PUT is still running) to prevent
            # storage corruption from overlapping OverwriteStorage saves.
            with transaction.atomic():
                file_obj = (
                    File.objects.select_for_update()
                    .filter(pk=self._file.pk)
                    .first()
                )
                if file_obj is None:
                    file_obj = self._file
                FileService.update_content(file_obj, content)
                self._file = file_obj
            logger.info(
                "webdav PUT done: user=%s path=%s size=%d elapsed=%.2fs",
                getattr(self._user, "username", "?"),
                self.path,
                size,
                time.monotonic() - getattr(self, "_write_started_at", time.monotonic()),
            )
        finally:
            buf.real_close()

    def delete(self):
        if getattr(self, "_moved", False):
            return  # Already moved in copy_move_single; nothing to delete.
        self._file.soft_delete()

    def copy_move_single(self, dest_path, *, is_move):
        dest_parts = dest_path.strip("/").split("/")
        new_name = dest_parts[-1]
        dest_parent = _resolve_parent(self._user, dest_parts[:-1])

        if is_move:
            if new_name != self._file.name:
                FileService.rename(self._file, new_name)
            if dest_parent != self._file.parent:
                self._file.parent = dest_parent
                self._file.save()
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
        file_obj.content.open("rb")
        content = DjangoFile(file_obj.content, name=new_name)

    try:
        return FileService.create_file(
            owner, new_name, parent=dest_parent,
            content=content, mime_type=file_obj.mime_type,
        )
    finally:
        if content is not None:
            file_obj.content.close()


def _resolve_parent(user, path_parts):
    """Resolve path segments to a parent folder in a single query."""
    if not path_parts:
        return None
    target_path = "/".join(path_parts)
    try:
        return File.objects.get(
            owner=user,
            path=target_path,
            node_type=File.NodeType.FOLDER,
            deleted_at__isnull=True,
        )
    except File.DoesNotExist:
        return None
