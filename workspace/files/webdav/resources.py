"""DAV resource classes wrapping the File model."""

import io
import logging
import os
import time

from django.conf import settings as django_settings
from django.core.files.base import File as DjangoFile
from django.db import transaction
from wsgidav.dav_error import DAVError, HTTP_BAD_REQUEST
from wsgidav.dav_provider import DAVCollection, DAVNonCollection

from workspace.files.models import File, file_upload_path
from workspace.files.services import FileService

logger = logging.getLogger(__name__)


class _StreamingWriteBuffer:
    """Write buffer that streams data directly to Django storage.

    Instead of buffering the entire file in ``/tmp`` via
    ``SpooledTemporaryFile``, this writes chunks directly to the final
    storage path on disk.  A small in-memory buffer (default 2 MB)
    accumulates data before each flush so the storage backend receives
    large sequential writes instead of many tiny ones.

    Because flushes block on the storage I/O, TCP backpressure propagates
    naturally: slow storage → slow ``write()`` → slow ``wsgi.input.read()``
    → TCP window shrinks → client slows down.  The result is a smooth
    progress bar on the client instead of "fast upload then stuck".
    """

    def __init__(self, full_path, flush_size):
        self._full_path = full_path
        self._flush_size = flush_size
        self._membuf = bytearray()
        self._total_size = 0
        self._fd = None
        self._open()

    def _open(self):
        os.makedirs(os.path.dirname(self._full_path), exist_ok=True)
        self._fd = os.open(
            self._full_path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o644,
        )

    def write(self, data):
        self._membuf.extend(data)
        self._total_size += len(data)
        if len(self._membuf) >= self._flush_size:
            self._flush()
        return len(data)

    def writelines(self, lines):
        for chunk in lines:
            self.write(chunk)

    def close(self):
        pass  # deferred — wsgidav calls close() before end_write()

    def _flush(self):
        if not self._membuf:
            return
        os.write(self._fd, self._membuf)
        self._membuf = bytearray()

    @property
    def size(self):
        return self._total_size

    def finalize(self):
        """Flush remaining data and close the file descriptor."""
        self._flush()
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def abort(self):
        """Close and delete the partially-written file."""
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            os.unlink(self._full_path)
        except OSError:
            logger.debug("Could not remove partial upload %s", self._full_path)


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
                FileService.accessible_files_q(self._user),
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
            FileService.accessible_files_q(self._user),
            name=name, parent__isnull=True,
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
                FileService.accessible_files_q(self._user),
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
        # Use accessible_files_q so we also find files created by other
        # members in group folders — not just files owned by self._user.
        file_obj = File.objects.filter(
            FileService.accessible_files_q(self._user),
            name=name, parent=self._file,
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

    @transaction.atomic
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
        storage = self._file.content.storage
        storage_path = file_upload_path(self._file, self._file.name)
        full_path = storage.path(storage_path)

        self._storage_path = storage_path
        self._write_buf = _StreamingWriteBuffer(
            full_path, DjangoFile.DEFAULT_CHUNK_SIZE,
        )
        self._write_started_at = time.monotonic()
        logger.info(
            "PUT started for %s by %s",
            self.path, getattr(self._user, "username", "?"),
        )
        return self._write_buf

    def end_write(self, *, with_errors):
        buf = self._write_buf
        elapsed = time.monotonic() - getattr(self, "_write_started_at", time.monotonic())
        username = getattr(self._user, "username", "?")

        if with_errors:
            buf.abort()
            logger.warning(
                "PUT failed for %s by %s (%.2fs)",
                self.path, username, elapsed,
            )
            # Only hard-delete if the record still exists and has never
            # had content (size is None).  Refresh first so we don't
            # delete a record that a concurrent PUT already populated.
            try:
                self._file.refresh_from_db()
                if self._file.size is None:
                    self._file.delete(hard=True)
            except File.DoesNotExist:
                pass  # already gone
            return

        # Detect partial uploads: if the client announced Content-Length
        # but we received fewer bytes, the connection was dropped
        # mid-transfer (e.g. Windows timeout). Reject so we don't save
        # a corrupted file.
        expected = int(self.environ.get("CONTENT_LENGTH") or 0)
        if expected and buf.size != expected:
            buf.abort()
            logger.warning(
                "PUT rejected for %s by %s: incomplete transfer "
                "(%d of %d bytes, %.2fs)",
                self.path, username, buf.size, expected, elapsed,
            )
            try:
                self._file.refresh_from_db()
                if self._file.size is None:
                    self._file.delete(hard=True)
            except File.DoesNotExist:
                pass  # already gone
            raise DAVError(HTTP_BAD_REQUEST, "Incomplete upload")

        # Finalize the file on storage (flush remaining buffer + close).
        buf.finalize()

        # Update DB metadata only — the file is already written to its
        # final storage path, so we just point content.name at it.
        # The record may have been hard-deleted by a concurrent retry's
        # end_write(with_errors=True) during our (slow) upload.  If so,
        # recreate it so the file on disk is not orphaned.
        with transaction.atomic():
            try:
                self._file.refresh_from_db()
            except File.DoesNotExist:
                logger.warning(
                    "File record deleted during upload for %s by %s, recreating",
                    self.path, username,
                )
                self._file = FileService.create_file(
                    self._user, self._file.name,
                    parent=self._file.parent,
                )
            self._file.size = buf.size
            self._file.mime_type = FileService.infer_mime_type(self._file.name)
            self._file.has_thumbnail = False
            self._file.content.name = self._storage_path
            self._file.save()

        logger.info(
            "PUT completed for %s by %s (%d bytes, %.2fs)",
            self.path, username,
            buf.size, time.monotonic() - self._write_started_at,
        )

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
        children = File.objects.filter(
            FileService.accessible_files_q(owner),
            parent=file_obj, deleted_at__isnull=True,
        )
        for child in children:
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
    return File.objects.filter(
        FileService.accessible_files_q(user),
        path=target_path,
        node_type=File.NodeType.FOLDER,
        deleted_at__isnull=True,
    ).first()
