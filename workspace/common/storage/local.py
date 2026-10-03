"""The local filesystem backend: Django's FileSystemStorage, plus the verbs it
lacks, in terms of ``os`` and ``shutil``.

Composed rather than inherited, and passed straight through for everything
FileSystemStorage already does - permissions, ``file_move_safe`` for large
uploads, ``O_EXCL`` on a name that must not be overwritten, ``safe_join`` -
so a disk behaves exactly as it does under a bare FileSystemStorage.

The one module allowed to turn a storage name into a filesystem path.
"""

import logging
import os
import shutil
import uuid
from contextlib import contextmanager

from django.core.files.storage import FileSystemStorage

from workspace.common.logging import scrub

from .backend import Backend, Entry, StagedWriter, checked_name

logger = logging.getLogger(__name__)


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

    def staged_writer(self, name):
        return _StagedFile(self._path(name))

    @contextmanager
    def local_path(self, name, *, max_bytes):
        # The blob itself: nothing is copied, so max_bytes has nothing to bound.
        path = self._path(name)
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        yield path


class _StagedFile(StagedWriter):
    """A sibling temp file renamed over the target on commit.

    Writing the target directly would truncate the current blob at the first
    byte of an overwrite: an interrupted upload (or its abort) would destroy
    the previous content, a concurrent reader would see a half-written file,
    and two concurrent writers (Windows retries a slow upload) would interleave
    their bytes.
    """

    def __init__(self, full_path):
        self._full_path = full_path
        self._temp_path = f"{full_path}.{uuid.uuid4().hex}.part"
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
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
        os.replace(self._temp_path, self._full_path)

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
