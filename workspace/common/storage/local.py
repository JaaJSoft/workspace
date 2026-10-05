"""The local filesystem backend: Django's FileSystemStorage, plus the verbs it
lacks, in terms of ``os`` and ``shutil``.

Composed rather than inherited, and passed straight through for everything
FileSystemStorage already does - permissions, ``file_move_safe`` for large
uploads, ``O_EXCL`` on a name that must not be overwritten, ``safe_join`` -
so a disk behaves exactly as it does under a bare FileSystemStorage.

The one module allowed to turn a storage name into a filesystem path.
"""

import errno
import logging
import os
import shutil
import uuid
from contextlib import contextmanager, suppress
from datetime import UTC, datetime

from django.core.files.storage import FileSystemStorage

from workspace.common.logging import scrub

from .backend import Backend, Blob, Entry, Moved, StagedWriter, checked_name

logger = logging.getLogger(__name__)

# Where staged writes wait for their commit: under the storage root, so a
# commit is a rename, but outside every tree a walk reads (the sync,
# copy_blobs, verify_file_storage). An upload in flight is never taken for a
# file, and one whose process died is not stranded in a user's folder, nor
# carried along when that folder is renamed.
STAGING_DIR = ".staging"


class LocalBackend(Backend):
    def __init__(self, *, allow_overwrite=False, **options):
        self._fs = FileSystemStorage(allow_overwrite=allow_overwrite, **options)

    def open(self, name, mode="rb"):
        return self._fs._open(name, mode)

    def save(self, name, content):
        # _save, not save: BlobStorage.save already resolved the name, and
        # FileSystemStorage.save would resolve it a second time.
        return self._fs._save(name, content)

    def delete(self, name):
        self._fs.delete(name)

    def exists(self, name):
        return self._fs.exists(name)

    def listdir(self, name):
        return self._fs.listdir(name)

    def size(self, name):
        return self._fs.size(name)

    def url(self, name):
        return self._fs.url(name)

    def path(self, name):
        return self._fs.path(name)

    def get_accessed_time(self, name):
        return self._fs.get_accessed_time(name)

    def get_created_time(self, name):
        return self._fs.get_created_time(name)

    def get_modified_time(self, name):
        return self._fs.get_modified_time(name)

    def _path(self, name):
        return self._fs.path(checked_name(name))

    def is_dir(self, name):
        return os.path.isdir(self._path(name))

    def is_file(self, name):
        return os.path.isfile(self._path(name))

    def scan(self, name):
        with os.scandir(self._path(name)) as entries:
            return [
                Entry(
                    name=entry.name,
                    is_dir=entry.is_dir(follow_symlinks=False),
                    is_file=entry.is_file(follow_symlinks=False),
                )
                for entry in entries
            ]

    def _walk(self, name):
        top = self._path(name)
        for current, dirs, files in os.walk(top):
            relative = os.path.relpath(current, top)
            prefix = (
                name if relative == "." else f"{name}/{relative.replace(os.sep, '/')}"
            )
            yield current, prefix, dirs, files

    def iter_blobs(self, name):
        for current, prefix, _dirs, files in self._walk(name):
            for filename in files:
                path = os.path.join(current, filename)
                # A symlink or a socket is not a blob the storage holds.
                if os.path.isfile(path) and not os.path.islink(path):
                    stat = os.stat(path)
                    yield Blob(
                        name=f"{prefix}/{filename}",
                        size=stat.st_size,
                        modified=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                    )

    def iter_dirs(self, name):
        for _current, prefix, dirs, _files in self._walk(name):
            for dirname in dirs:
                yield f"{prefix}/{dirname}"

    def make_dir(self, name):
        os.makedirs(self._path(name), exist_ok=True)

    def remove_dir_if_empty(self, name):
        full_path = self._path(name)
        if not os.path.isdir(full_path) or os.listdir(full_path):
            return False
        os.rmdir(full_path)
        return True

    def delete_prefix(self, name):
        full_path = self._path(name)
        if not os.path.isdir(full_path):
            return False
        shutil.rmtree(full_path)
        return True

    def move(self, source, destination, *, overwrite=False):
        source_path = self._path(source)
        destination_path = self._path(destination)
        os.makedirs(os.path.dirname(destination_path), exist_ok=True)
        # os.rename refuses an existing destination on Windows; os.replace
        # overwrites it everywhere. POSIX makes the two the same call.
        if overwrite:
            os.replace(source_path, destination_path)
        else:
            os.rename(source_path, destination_path)

    def relocate(self, source, destination):
        # A rename is final the moment it returns: nothing is left to drop.
        self.move(source, destination)
        return Moved()

    def staged_writer(self, name):
        return _StagedFile(self, self._path(name))

    def _staging_dir(self):
        return os.path.join(self._fs.location, STAGING_DIR)

    def purge_staged(self, before):
        cutoff = before.timestamp()
        try:
            entries = list(os.scandir(self._staging_dir()))
        except FileNotFoundError:
            return 0
        purged = 0
        for entry in entries:
            try:
                if (
                    entry.is_file(follow_symlinks=False)
                    and entry.stat(follow_symlinks=False).st_mtime < cutoff
                ):
                    os.unlink(entry.path)
                    purged += 1
            except FileNotFoundError:
                continue  # committed or aborted since it was listed
        return purged

    @contextmanager
    def local_path(self, name, *, max_bytes):
        # The blob itself: nothing is copied, so max_bytes has nothing to bound.
        path = self._path(name)
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        yield path


class _StagedFile(StagedWriter):
    """A temp file in the staging directory, renamed over the target on commit.

    Writing the target directly would truncate the current blob at the first
    byte of an overwrite: an interrupted upload (or its abort) would destroy
    the previous content, a concurrent reader would see a half-written file,
    and two concurrent writers (Windows retries a slow upload) would interleave
    their bytes.
    """

    def __init__(self, backend, full_path):
        self._full_path = full_path
        self._permissions = backend._fs.file_permissions_mode
        staging = backend._staging_dir()
        os.makedirs(staging, mode=0o700, exist_ok=True)
        self._temp_path = os.path.join(staging, f"{uuid.uuid4().hex}.part")
        self._fd = os.open(
            self._temp_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )

    def write(self, data):
        # os.write may write fewer bytes than requested (POSIX); loop or the
        # unwritten tail is silently dropped.
        view = memoryview(data)
        while view:
            written = os.write(self._fd, view)
            view = view[written:]

    def commit(self):
        self._close()
        if self._permissions is not None:
            os.chmod(self._temp_path, self._permissions)
        os.makedirs(os.path.dirname(self._full_path), exist_ok=True)
        try:
            os.replace(self._temp_path, self._full_path)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            self._replace_across_filesystems()

    def _replace_across_filesystems(self):
        # The target is on a volume mounted inside the storage root, which no
        # rename from the staging directory reaches: copy beside the target
        # first, so the name still changes in one step.
        sibling = f"{self._full_path}.{uuid.uuid4().hex}.part"
        try:
            shutil.copy2(self._temp_path, sibling)
            os.replace(sibling, self._full_path)
        except BaseException:
            with suppress(OSError):
                os.unlink(sibling)
            raise
        with suppress(OSError):
            os.unlink(self._temp_path)

    def abort(self):
        self._close()
        try:
            os.unlink(self._temp_path)
        except OSError:
            logger.debug("Could not remove partial upload %s", scrub(self._temp_path))

    def _close(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
