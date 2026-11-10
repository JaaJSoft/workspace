"""DAV resource classes wrapping the File model."""

import errno
import functools
import io
import logging
import time
from contextlib import contextmanager

from django.core.files.base import File as DjangoFile
from django.db import transaction
from wsgidav.dav_error import (
    HTTP_BAD_REQUEST,
    HTTP_CONFLICT,
    HTTP_FORBIDDEN,
    HTTP_INSUFFICIENT_STORAGE,
    HTTP_LOCKED,
    HTTP_PRECONDITION_FAILED,
    DAVError,
)
from wsgidav.dav_provider import DAVCollection, DAVNonCollection

from workspace.common.logging import scrub
from workspace.files.models import (
    File,
    file_upload_path,
    files_storage,
    name_forms,
)
from workspace.files.queries import FilePermission, accessible_files_q, get_permission
from workspace.files.services import FileService, quota
from workspace.files.services.content_hash import new_hasher
from workspace.files.services.locking import (
    LockConflict,
    Relocated,
    StaleContent,
    conflicting_lock,
)
from workspace.files.services.refusals import Refusal, Refused
from workspace.files.services.transfers import (
    OnConflict,
    copy_item,
    move_item,
    move_onto,
)
from workspace.files.services.trash import trash_item

logger = logging.getLogger(__name__)


def dav_nodes(*args, **kwargs):
    """Live nodes WebDAV serves, filtered by *args* and *kwargs*.

    Shortcuts are left out: a client would take one for an empty file, and
    every byte it wrote into it would land on a node that holds none.
    """
    return File.objects.filter(*args, deleted_at__isnull=True, **kwargs).exclude(
        node_type=File.NodeType.SHORTCUT
    )


def _require_permission(user, file_obj, level, verb):
    """Raise 403 unless *user* holds *level* on *file_obj*.

    The levels are the REST API's: replacing a file's content needs WRITE (a
    read-write share), creating, renaming, moving or deleting needs EDIT.
    ``None`` stands for the user's own root.
    """
    if file_obj is None:
        return
    permission = get_permission(user, file_obj)
    if permission is None or permission < level:
        raise DAVError(HTTP_FORBIDDEN, f"{verb} is not allowed here")


# How a refusal of the file services reads to a WebDAV client; anything else is
# a 403.
_DAV_STATUS = {
    Refusal.LOCKED: HTTP_LOCKED,
    Refusal.QUOTA: HTTP_INSUFFICIENT_STORAGE,
    Refusal.NAME_TAKEN: HTTP_PRECONDITION_FAILED,
    Refusal.STALE: HTTP_PRECONDITION_FAILED,
}


@contextmanager
def _dav_refusals(verb):
    """Answer a refused move, copy or delete the way a WebDAV client expects."""
    try:
        yield
    except Refused as refusal:
        if refusal.reason == Refusal.LOCKED:
            holder = getattr(refusal.holder, "username", "another user")
            message = f"File is locked by {holder}"
        elif refusal.reason == Refusal.QUOTA:
            message = str(refusal.__cause__)
        else:
            message = f"{verb} is not allowed here"
        raise DAVError(
            _DAV_STATUS.get(refusal.reason, HTTP_FORBIDDEN), message
        ) from refusal


def _refuse_overlong_names(method):
    """Answer a name the storage cannot hold with a 400, not a server error."""

    @functools.wraps(method)
    def wrapper(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except OSError as exc:
            if exc.errno != errno.ENAMETOOLONG:
                raise
            raise DAVError(HTTP_BAD_REQUEST, "Name too long to be stored.") from exc

    return wrapper


class _StreamingWriteBuffer:
    """Write buffer that streams data straight into a staged storage write.

    Instead of buffering the entire file in ``/tmp`` via
    ``SpooledTemporaryFile``, this hands chunks to the storage as they
    arrive.  A small in-memory buffer accumulates data before each flush so
    the storage backend receives large sequential writes instead of many
    tiny ones.

    Because flushes block on the storage I/O, TCP backpressure propagates
    naturally: slow storage → slow ``write()`` → slow ``wsgi.input.read()``
    → TCP window shrinks → client slows down.  The result is a smooth
    progress bar on the client instead of "fast upload then stuck".

    The bytes only replace the current blob on ``finalize()``, which commits
    the staged write: an interrupted or refused PUT leaves the previous
    content where it was.
    """

    def __init__(self, writer, flush_size, max_bytes=None):
        self._writer = writer
        self._flush_size = flush_size
        self._max_bytes = max_bytes
        self._membuf = bytearray()
        self._total_size = 0
        self._hasher = new_hasher()

    def write(self, data):
        # Per chunk, not at the end: this buffer writes straight to storage, so
        # a client ignoring the advertised quota would fill the volume first.
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
        self._writer.write(self._membuf)
        self._membuf = bytearray()

    @property
    def size(self):
        return self._total_size

    @property
    def content_hash(self):
        return self._hasher.hexdigest()

    def finalize(self):
        """Flush remaining data and publish the blob in one step.

        On failure the staged bytes are dropped: wsgidav calls end_write once,
        and nothing else would remove them - a ``.part`` file the sync would
        adopt as the user's, or a multipart upload left open in the bucket.
        """
        try:
            self._flush()
            self._writer.commit()
        except BaseException:
            self._writer.abort()
            raise

    def abort(self):
        """Drop the staged bytes; the previous content (if any) survives."""
        self._writer.abort()


class _Collection(DAVCollection):
    """A folder of the tree, or the user's root when ``_file`` is None."""

    _file = None

    def __init__(self, path, environ):
        super().__init__(path, environ)
        self._user = environ["workspace.user"]

    def get_display_info(self):
        return {"type": "Directory"}

    def get_member_names(self):
        return [f.name for f in self._members()]

    def get_member(self, name):
        forms = name_forms(name)
        for f in self._members():
            if f.name in forms:
                return self._wrap(f)
        return None

    def get_member_list(self):
        return [self._wrap(f) for f in self._members()]

    def _members(self):
        if not hasattr(self, "_members_cache"):
            # The access filter joins the shares: a node comes back once per
            # share it has.
            self._members_cache = list(
                dav_nodes(
                    accessible_files_q(self._user),
                    parent=self._file,
                ).distinct()
            )
        return self._members_cache

    def _wrap(self, file_obj):
        child_path = self.path.rstrip("/") + "/" + file_obj.name
        if file_obj.is_folder():
            return FolderResource(child_path, self.environ, file_obj)
        return FileResource(child_path, self.environ, file_obj)

    @_refuse_overlong_names
    def create_empty_resource(self, name):
        # Reuse an existing file to avoid duplicates from concurrent PUTs
        # (e.g. Windows retries while a slow upload is still in progress).
        # Through accessible_files_q, so a file another member created in a
        # group folder is found too, not just those self._user owns.
        file_obj = File.objects.filter(
            accessible_files_q(self._user),
            name__in=name_forms(name),
            parent=self._file,
            node_type=File.NodeType.FILE,
            deleted_at__isnull=True,
        ).first()
        if file_obj is None:
            _require_permission(self._user, self._file, FilePermission.EDIT, "PUT")
            file_obj = FileService.create_file(
                self._user,
                name,
                parent=self._file,
                acting_user=self._user,
            )
        child_path = self.path.rstrip("/") + "/" + name
        return FileResource(child_path, self.environ, file_obj)

    @_refuse_overlong_names
    def create_collection(self, name):
        _require_permission(self._user, self._file, FilePermission.EDIT, "MKCOL")
        FileService.create_folder(
            self._user,
            name,
            parent=self._file,
            acting_user=self._user,
        )
        return True

    def _group_id(self):
        return self._file.group_id if self._file is not None else None

    def get_used_bytes(self):
        return _advertised_space(self.environ, self._user, self._group_id())[0]

    def get_available_bytes(self):
        return _advertised_space(self.environ, self._user, self._group_id())[1]


class RootCollection(_Collection):
    """Virtual root representing the user's top-level files/folders."""

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


class FolderResource(_Collection):
    """Wraps a ``File(node_type=FOLDER)`` instance."""

    def __init__(self, path, environ, file_obj):
        super().__init__(path, environ)
        self._file = file_obj

    def get_creation_date(self):
        return self._file.created_at.timestamp()

    def get_last_modified(self):
        return self._file.updated_at.timestamp()

    def delete(self):
        with _dav_refusals("DELETE"):
            trash_item(self._user, self._file)

    @_refuse_overlong_names
    def copy_move_single(self, dest_path, *, is_move):
        # A move is always recursive here (support_recursive_move), so this
        # is a copy.
        if _inside_copied_tree(self.environ, dest_path):
            return
        dest_parts = _dest_parts(dest_path)
        dest_parent = _resolve_parent(self._user, dest_parts[:-1])
        if self.environ.get("HTTP_DEPTH") == "0":
            # The collection without its members: an empty folder.
            _require_permission(self._user, dest_parent, FilePermission.EDIT, "COPY")
            FileService.create_folder(
                self._user, dest_parts[-1], parent=dest_parent, acting_user=self._user
            )
            return
        # WsgiDAV visits every descendant after this one: the whole tree is
        # copied here, in one go, and the visits that follow find it done.
        _copy_to(self._file, self._user, dest_path)
        self.environ.setdefault("workspace.copied_trees", []).append(
            "/".join(dest_parts)
        )

    def support_recursive_move(self, dest_path):
        return True

    @_refuse_overlong_names
    def move_recursive(self, dest_path):
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
        from workspace.files.services.scanning.policy import blocked_reason

        reason = blocked_reason(self._file)
        if reason is not None:
            logger.warning(
                "WebDAV GET refused for quarantined %s (%s)",
                scrub(self.path),
                scrub(reason),
            )
            raise DAVError(HTTP_FORBIDDEN, "File is quarantined")
        if not self._file.content:
            return io.BytesIO(b"")
        self._file.content.open("rb")
        return self._file.content

    def _refuse_if_locked(self, verb):
        """Raise 423 when someone else holds the app lock on this file.

        wsgidav keeps its own lock table, and until ``webdav/locks.py`` mirrors
        a DAV lock onto the File row the two tables describe the same file
        independently. A mounted client that never issues LOCK bypasses both,
        which is why the refusal sits on the write itself rather than on the
        LOCK method alone.
        """
        holder = conflicting_lock(self._file, self._user)
        if holder is None:
            return
        logger.info(
            "WebDAV %s refused for %s by %s: locked by %s",
            verb,
            scrub(self.path),
            scrub(getattr(self._user, "username", "?")),
            scrub(holder.username),
        )
        raise DAVError(HTTP_LOCKED, f"File is locked by {holder.username}")

    @_refuse_overlong_names
    def begin_write(self, content_type=None):
        _require_permission(self._user, self._file, FilePermission.WRITE, "PUT")
        self._refuse_if_locked("PUT")

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

        # A client that sent If-Match asked to be refused rather than to
        # overwrite a newer version; wsgidav has already checked it against the
        # ETag for this request. Carrying the version it matched into the write
        # is what makes the promise survive an upload that takes minutes - the
        # window an If-Match on its own leaves wide open.
        if_match = (self.environ.get("HTTP_IF_MATCH") or "").strip()
        self._expected_hash = (
            self._file.content_hash if if_match and if_match != "*" else None
        )

        storage_path = file_upload_path(self._file, self._file.name)
        self._storage_path = storage_path
        self._write_buf = _StreamingWriteBuffer(
            self._file.content.storage.staged_writer(storage_path),
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

        # The row goes first and the bytes follow: the blob still sits in its
        # temp file, so a refused write leaves the previous content exactly
        # where it was instead of having already replaced it.
        # The record may have been hard-deleted by a concurrent retry's
        # end_write(with_errors=True) during our (slow) upload.  If so,
        # recreate it so the file on disk is not orphaned.
        refusal = None
        with transaction.atomic():
            try:
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
                # The bytes are staged under the path the file had when the PUT
                # began; a rename, a move or a trip to the trash since has its
                # blob somewhere else.
                if (
                    self._file.deleted_at is not None
                    or file_upload_path(self._file, self._file.name)
                    != self._storage_path
                ):
                    raise Relocated()
                FileService.replace_content_storage(
                    self._file,
                    storage_path=self._storage_path,
                    size=buf.size,
                    content_hash=buf.content_hash,
                    acting_user=self._user,
                    expected_hash=getattr(self, "_expected_hash", None),
                )
            except (LockConflict, StaleContent, Relocated) as exc:
                buf.abort()
                refusal = exc
            except BaseException:
                buf.abort()
                raise
            else:
                # Flush the remaining buffer and move the blob into place.
                # Inside the transaction: a storage failure here has to take
                # the row that already points at it down with it.
                buf.finalize()
        if refusal is not None:
            # The empty row do_PUT created for a new file stays behind a
            # refused upload unless it goes now, outside the block it would
            # have been rolled back with.
            self._discard_placeholder()
            self._refusal_error(refusal, username)

        logger.info(
            "PUT completed for %s by %s (%d bytes, %.2fs)",
            scrub(self.path),
            username,
            buf.size,
            time.monotonic() - self._write_started_at,
        )

    def _refusal_error(self, exc, username):
        """Raise the DAV answer a refused content write owes the client."""
        logger.info(
            "PUT refused for %s by %s: %s",
            scrub(self.path),
            username,
            scrub(str(exc)),
        )
        if isinstance(exc, LockConflict):
            holder = getattr(exc.holder, "username", "another user")
            raise DAVError(HTTP_LOCKED, f"File is locked by {holder}") from exc
        if isinstance(exc, Relocated):
            raise DAVError(
                HTTP_CONFLICT, "The file was moved while it was being uploaded."
            ) from exc
        raise DAVError(
            HTTP_PRECONDITION_FAILED, "The file changed since it was loaded."
        ) from exc

    def delete(self):
        if getattr(self, "_moved", False):
            return  # Already moved in copy_move_single; nothing to delete.
        # A MOVE that handle_move hands back to wsgidav deletes an existing
        # destination first, so this is also where that overwrite meets the
        # lock.
        with _dav_refusals("DELETE"):
            trash_item(self._user, self._file)

    def handle_move(self, dest_path):
        """Save-by-rename: a MOVE over an existing file replaces its content.

        Editors save by writing a temp file and moving it over the document.
        wsgidav's default trashes the destination first, which charges the
        bucket a full copy per save and strands the document's shares, tags
        and comments on the trashed row. Returning ``False`` hands every other
        MOVE back to wsgidav.
        """
        target = self.provider.get_resource_inst(dest_path, self.environ)
        if not isinstance(target, FileResource) or target._file.pk == self._file.pk:
            return False
        # Nothing to hand over: wsgidav's own MOVE trashes the target instead.
        if not self._file.content:
            return False
        with _dav_refusals("MOVE"):
            move_onto(self._user, self._file, target._file)
        return True

    @_refuse_overlong_names
    def copy_move_single(self, dest_path, *, is_move):
        if is_move:
            _move_to(self._file, self._user, dest_path)
            self._moved = True
        elif not _inside_copied_tree(self.environ, dest_path):
            _copy_to(self._file, self._user, dest_path)

    def support_content_length(self):
        return True

    def support_recursive_move(self, dest_path):
        return False

    def prevent_locking(self):
        """Refuse a DAV LOCK while another user holds the app lock.

        wsgidav answers 403 rather than the 423 a lock conflict deserves, but
        refusing is what matters: a client that took the DAV lock here would
        believe it owns a file the browser editor is holding. A user who may
        not write the file has no business locking others out of it either.
        """
        permission = get_permission(self._user, self._file)
        if permission is None or permission < FilePermission.WRITE:
            return True
        return conflicting_lock(self._file, self._user) is not None

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


# What a store with no limit of its own advertises (object storage): every
# client takes a figure at face value, and one that gets none makes up its own.
_NO_LIMIT_FREE_BYTES = 2**50


def _disk_free_bytes():
    free = files_storage().free_bytes()
    return _NO_LIMIT_FREE_BYTES if free is None else free


def _advertised_space(environ, user, group_id):
    """``(used, available)`` for the bucket a write into a collection lands in.

    Every collection answers, an unlimited bucket included: a client that
    gets no {DAV:}quota-available-bytes makes up a figure of its own (the
    Windows redirector reports the local system drive) and refuses copies on
    it. A new file is charged to its folder's group, else to whoever writes
    it - a folder someone shared with *user* included - so no other user's
    usage is ever disclosed. A group's figures stay with its members: someone
    reaching a group folder through a share only learns the free disk space.
    ``available`` is capped by the disk: a quota the volume cannot hold is no
    promise.

    wsgidav asks both questions for every collection of a listing, twice on
    an allprop PROPFIND, so each bucket is resolved once per request.
    """
    cache = environ.setdefault("workspace.advertised_space", {})
    key = ("group", group_id) if group_id else ("user", user.pk)
    if key not in cache:
        if group_id and not user.groups.filter(pk=group_id).exists():
            cache[key] = (None, _disk_free_bytes())
            return cache[key]
        if group_id:
            used = quota.group_usage(group_id)
            limit = quota.effective_group_quota(group_id)
        else:
            used = quota.personal_usage(user)
            limit = quota.effective_quota(user)
        available = _disk_free_bytes()
        if limit is not None:
            remaining = max(0, limit - used)
            available = remaining if available is None else min(remaining, available)
        cache[key] = (used, available)
    return cache[key]


def _move_to(file_obj, user, dest_path):
    """Move and/or rename *file_obj* to *dest_path* (MOVE handlers).

    wsgidav has already trashed an existing destination when the client
    asked to overwrite it, so a name still taken is refused.
    """
    dest_parts = _dest_parts(dest_path)
    dest_parent = _resolve_parent(user, dest_parts[:-1])
    with _dav_refusals("MOVE"):
        move_item(user, file_obj, dest_parent, OnConflict.ERROR, name=dest_parts[-1])


def _copy_to(file_obj, user, dest_path):
    """Copy *file_obj*, with what it holds, to *dest_path* (COPY handlers)."""
    dest_parts = _dest_parts(dest_path)
    dest_parent = _resolve_parent(user, dest_parts[:-1])
    with _dav_refusals("COPY"):
        copy_item(user, file_obj, dest_parent, OnConflict.ERROR, name=dest_parts[-1])


def _inside_copied_tree(environ, dest_path):
    """Whether *dest_path* lies in a folder this COPY already copied whole."""
    dest = "/".join(_dest_parts(dest_path))
    return any(
        dest.startswith(f"{tree}/")
        for tree in environ.get("workspace.copied_trees", ())
    )


def _resolve_parent(user, path_parts):
    """Resolve path segments to a parent folder in a single query."""
    if not path_parts:
        return None
    target_path = "/".join(path_parts)
    return File.objects.filter(
        accessible_files_q(user),
        path__in=name_forms(target_path),
        node_type=File.NodeType.FOLDER,
        deleted_at__isnull=True,
    ).first()
