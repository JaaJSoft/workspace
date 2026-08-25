"""DAV resource classes wrapping the File model."""

import io
import logging
import os
import time
import uuid
from contextlib import contextmanager

from django.core.files.base import File as DjangoFile
from django.db import transaction
from wsgidav.dav_error import HTTP_BAD_REQUEST, HTTP_INSUFFICIENT_STORAGE, DAVError
from wsgidav.dav_provider import DAVCollection, DAVNonCollection

from workspace.common.logging import scrub
from workspace.files.models import File, file_upload_path
from workspace.files.services import FileService, quota
from workspace.files.services.content_hash import new_hasher

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

    Bytes land in a unique sibling temp file that ``finalize()`` atomically
    renames over ``full_path``.  Writing to the final path directly would
    truncate the current blob at the first byte of an overwrite PUT: an
    interrupted upload (or its ``abort()``) would then destroy the previous
    content, a concurrent GET would read a half-written file, and two
    concurrent PUTs (Windows retries a slow upload) would interleave writes.
    """

    def __init__(self, full_path, flush_size, max_bytes=None):
        self._full_path = full_path
        self._temp_path = f"{full_path}.{uuid.uuid4().hex}.part"
        self._flush_size = flush_size
        self._max_bytes = max_bytes
        self._membuf = bytearray()
        self._total_size = 0
        self._hasher = new_hasher()
        self._fd = None
        self._open()

    def _open(self):
        os.makedirs(os.path.dirname(self._full_path), exist_ok=True)
        self._fd = os.open(
            self._temp_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )

    def write(self, data):
        # Per chunk, not at the end: this buffer writes straight to disk, so a
        # client ignoring the advertised quota would fill the volume first.
        # wsgidav answers the DAVError with end_write(with_errors=True), which
        # aborts and cleans up, then a 507.
        if (
            self._max_bytes is not None
            and self._total_size + len(data) > self._max_bytes
        ):
            raise DAVError(HTTP_INSUFFICIENT_STORAGE, "Storage quota exceeded")
        self._membuf.extend(data)
        self._total_size += len(data)
        self._hasher.update(data)
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
        # os.write may write fewer bytes than requested (POSIX); loop or
        # the unwritten tail is silently dropped.
        view = memoryview(self._membuf)
        while view:
            written = os.write(self._fd, view)
            view = view[written:]
        self._membuf = bytearray()

    @property
    def size(self):
        return self._total_size

    @property
    def content_hash(self):
        return self._hasher.hexdigest()

    def finalize(self):
        """Flush remaining data, close, and move into place atomically."""
        self._flush()
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        os.replace(self._temp_path, self._full_path)

    def abort(self):
        """Close and delete the partially-written temp file.

        The final path is never touched, so the previous content (if any)
        survives an aborted overwrite.
        """
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            os.unlink(self._temp_path)
        except OSError:
            logger.debug("Could not remove partial upload %s", scrub(self._temp_path))


class RootCollection(DAVCollection):
    """Virtual root representing the user's top-level files/folders."""

    def __init__(self, path, environ):
        super().__init__(path, environ)
        self._user = environ["workspace.user"]

    def get_display_info(self):
        return {"type": "Directory"}

    def get_creation_date(self):
        # davfs2 marks the directory cache invalid when the parent
        # collection lacks ``creationdate`` / ``getlastmodified`` —
        # readdir then returns EINVAL and open(O_CREAT) returns EIO.
        # Use the user's join date: stable, never zero, no extra query.
        return self._user.date_joined.timestamp()

    def get_last_modified(self):
        return self._user.date_joined.timestamp()

    def get_display_name(self):
        # WsgiDAV defaults to the URL basename, which is empty for the
        # root collection — Windows Mini-Redirector then shows a blank
        # entry in Explorer.  A static label avoids that.
        return "workspace"

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
            name=name,
            parent__isnull=True,
            node_type=File.NodeType.FILE,
            deleted_at__isnull=True,
        ).first()
        if file_obj is None:
            file_obj = FileService.create_file(
                self._user,
                name,
                parent=None,
                acting_user=self._user,
            )
        child_path = self.path.rstrip("/") + "/" + name
        return FileResource(child_path, self.environ, file_obj)

    def create_collection(self, name):
        FileService.create_folder(self._user, name, parent=None, acting_user=self._user)
        return True

    def get_used_bytes(self):
        return quota.personal_usage(self._user)

    def get_available_bytes(self):
        # wsgidav omits {DAV:}quota-available-bytes when this returns None,
        # which is exactly what an unlimited bucket should advertise.
        remaining = quota.remaining_bytes(owner=self._user, group=None)
        return None if remaining is None else max(0, remaining)


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

    def _bucket_bytes(self):
        """``(used, available)`` for this folder, or ``(None, None)``.

        Only a group root is a bucket; a sub-folder is part of a total that is
        reported one level up. wsgidav asks both questions for every resource
        of a listing, twice on an allprop PROPFIND, so the pair is computed
        once per resource - and a resource lives for one request.
        """
        if not (self._file.group_id and self._file.parent_id is None):
            return None, None
        if not hasattr(self, "_bucket_cache"):
            used = quota.group_usage(self._file.group_id)
            limit = quota.effective_group_quota(self._file.group_id)
            self._bucket_cache = (
                used,
                None if limit is None else max(0, limit - used),
            )
        return self._bucket_cache

    def get_used_bytes(self):
        return self._bucket_bytes()[0]

    def get_available_bytes(self):
        return self._bucket_bytes()[1]

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
            name=name,
            parent=self._file,
            node_type=File.NodeType.FILE,
            deleted_at__isnull=True,
        ).first()
        if file_obj is None:
            file_obj = FileService.create_file(
                self._user,
                name,
                parent=self._file,
                acting_user=self._user,
            )
        child_path = self.path.rstrip("/") + "/" + name
        return FileResource(child_path, self.environ, file_obj)

    def create_collection(self, name):
        FileService.create_folder(
            self._user,
            name,
            parent=self._file,
            acting_user=self._user,
        )
        return True

    def delete(self):
        FileService.soft_delete(self._file, acting_user=self._user)

    def copy_move_single(self, dest_path, *, is_move):
        # WsgiDAV's copy/move loop visits every descendant itself, so this
        # hook must only create the destination collection, without members
        # (recursing here would duplicate every child).
        dest_parts = _dest_parts(dest_path)
        new_name = dest_parts[-1]
        dest_parent = _resolve_parent(self._user, dest_parts[:-1])
        FileService.create_folder(
            self._user,
            new_name,
            parent=dest_parent,
            acting_user=self._user,
        )

    def support_recursive_move(self, dest_path):
        return True

    @transaction.atomic
    def move_recursive(self, dest_path):
        with _as_insufficient_storage():
            _move_to(self._file, self._user, dest_path)

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
        return {"type": self._file.type or "File"}

    def get_content(self):
        if not self._file.content:
            return io.BytesIO(b"")
        self._file.content.open("rb")
        return self._file.content

    def begin_write(self, content_type=None):
        # An overwrite frees the bytes it replaces, so credit them back before
        # deciding how much room is left.
        remaining = quota.remaining_bytes(
            owner=self._file.owner_id, group=self._file.group_id
        )
        ceiling = None
        if remaining is not None:
            # The bytes already held are always writable again, even when an
            # administrator lowered the quota below current usage and left
            # `remaining` negative - a shrinking file must stay saveable.
            ceiling = (self._file.size or 0) + max(0, remaining)
            declared = int(self.environ.get("CONTENT_LENGTH") or 0)
            if declared > ceiling:
                raise DAVError(HTTP_INSUFFICIENT_STORAGE, "Storage quota exceeded")

        storage = self._file.content.storage
        storage_path = file_upload_path(self._file, self._file.name)
        full_path = storage.path(storage_path)

        self._storage_path = storage_path
        self._write_buf = _StreamingWriteBuffer(
            full_path,
            DjangoFile.DEFAULT_CHUNK_SIZE,
            max_bytes=ceiling,
        )
        self._write_started_at = time.monotonic()
        logger.info(
            "PUT started for %s by %s",
            scrub(self.path),
            scrub(getattr(self._user, "username", "?")),
        )
        return self._write_buf

    def _discard_placeholder(self):
        """Drop the row ``create_empty_resource`` left behind, if still empty.

        Only a record that never had content (``size is None``) goes; refresh
        first so a concurrent PUT that already populated it survives.
        """
        try:
            self._file.refresh_from_db()
            if self._file.size is None:
                self._file.delete(hard=True)
        except File.DoesNotExist:
            pass  # already gone

    def end_write(self, *, with_errors):
        # No buffer means begin_write refused the upload before opening one.
        # do_PUT still creates the empty row first, so the failure path must
        # run all the same.
        buf = getattr(self, "_write_buf", None)
        started_at = getattr(self, "_write_started_at", None)
        elapsed = 0.0 if started_at is None else time.monotonic() - started_at
        username = scrub(getattr(self._user, "username", "?"))

        if with_errors or buf is None:
            if buf is not None:
                buf.abort()
            logger.warning(
                "PUT failed for %s by %s (%.2fs)",
                scrub(self.path),
                username,
                elapsed,
            )
            self._discard_placeholder()
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
                scrub(self.path),
                username,
                buf.size,
                expected,
                elapsed,
            )
            self._discard_placeholder()
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
                    scrub(self.path),
                    username,
                )
                self._file = FileService.create_file(
                    self._user,
                    self._file.name,
                    parent=self._file.parent,
                    acting_user=self._user,
                )
            FileService.replace_content_storage(
                self._file,
                storage_path=self._storage_path,
                size=buf.size,
                content_hash=buf.content_hash,
                acting_user=self._user,
            )

        logger.info(
            "PUT completed for %s by %s (%d bytes, %.2fs)",
            scrub(self.path),
            username,
            buf.size,
            time.monotonic() - self._write_started_at,
        )

    def delete(self):
        if getattr(self, "_moved", False):
            return  # Already moved in copy_move_single; nothing to delete.
        FileService.soft_delete(self._file, acting_user=self._user)

    def copy_move_single(self, dest_path, *, is_move):
        if is_move:
            with transaction.atomic(), _as_insufficient_storage():
                _move_to(self._file, self._user, dest_path)
            self._moved = True
        else:
            dest_parts = _dest_parts(dest_path)
            new_name = dest_parts[-1]
            dest_parent = _resolve_parent(self._user, dest_parts[:-1])
            with _as_insufficient_storage():
                _copy_as(self._file, dest_parent, self._user, new_name)

    def support_content_length(self):
        return True

    def support_recursive_move(self, dest_path):
        return False

    def support_etag(self):
        return True

    def get_etag(self):
        # WsgiDAV's contract (util.checked_etag): return the bare value
        # without quotes — wsgidav adds them when serializing the HTTP
        # ``ETag:`` header.  Returning a quoted string here triggers a
        # 500.
        return f"{self._file.uuid}-{self._file.updated_at.timestamp()}"


def _dest_parts(dest_path):
    """Split a WsgiDAV destination path into non-empty segments.

    WsgiDAV normalizes collection destinations with a trailing slash and
    builds descendant destinations by concatenation, so paths like
    ``/dst//child`` reach the resource hooks during folder copies.  Without
    filtering, parent resolution would chase a ``dst/`` path that doesn't
    exist and silently re-root the copy.
    """
    return [part for part in dest_path.split("/") if part]


@contextmanager
def _as_insufficient_storage():
    """Translate a refused write into the 507 a WebDAV client expects."""
    try:
        yield
    except quota.QuotaExceeded as exc:
        raise DAVError(HTTP_INSUFFICIENT_STORAGE, str(exc)) from exc


def _move_to(file_obj, user, dest_path):
    """Move and/or rename *file_obj* to *dest_path* (MOVE handlers).

    When both a rename and a re-parent are needed, the order matters:
    renaming first collides with an old-parent sibling already named
    ``new_name`` (folder directories cannot be merged on storage, and the
    descendants' content paths would be rewritten to a directory that was
    never created), while moving first collides with a destination child
    carrying the current name.  Pick whichever order is collision-free;
    renaming first is the default.
    """
    dest_parts = _dest_parts(dest_path)
    new_name = dest_parts[-1]
    dest_parent = _resolve_parent(user, dest_parts[:-1])

    needs_rename = new_name != file_obj.name
    needs_move = dest_parent != file_obj.parent

    if needs_move:
        # FileService.rename below moves the blob with a non-transactional
        # os.rename, so a refusal from move() would leave it stranded. Refuse here,
        # before anything is written.
        FileService.check_move_allowed(file_obj, dest_parent, acting_user=user)

    rename_first = not (
        needs_rename
        and needs_move
        and _live_child_exists(user, file_obj.parent, new_name)
    )

    if needs_rename and rename_first:
        FileService.rename(file_obj, new_name, acting_user=user)
    if needs_move:
        FileService.move(file_obj, dest_parent, acting_user=user)
    if needs_rename and not rename_first:
        FileService.rename(file_obj, new_name, acting_user=user)


def _live_child_exists(user, parent, name):
    return File.objects.filter(
        FileService.accessible_files_q(user),
        parent=parent,
        name=name,
        deleted_at__isnull=True,
    ).exists()


def _copy_as(file_obj, dest_parent, owner, new_name):
    """Copy a (non-folder) File to *dest_parent* with a specific *new_name*.

    Unlike ``FileService.copy`` this does not auto-generate "(Copy)" suffixes.
    Folders are never copied here: WsgiDAV's copy loop creates each
    collection via ``FolderResource.copy_move_single`` and visits the
    descendants one by one.
    """
    content = None
    if file_obj.content:
        file_obj.content.open("rb")
        content = DjangoFile(file_obj.content, name=new_name)

    try:
        return FileService.create_file(
            owner,
            new_name,
            parent=dest_parent,
            content=content,
            mime_type=file_obj.mime_type,
            acting_user=owner,
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
