"""The contract every blob backend implements.

Django's Storage API stops at open/save/delete/exists/listdir/size/url. It has
no directories, no way to move a name, and no write that only appears once it
is whole - and a backend without local paths answers all three differently
from a disk: a directory is a key prefix, a move is a copy then a delete. Each
backend implements the lot once; the rest of the app holds a ``BlobStorage``
(``facade.py``), never a backend.

Names are storage names, ``/``-separated whatever the platform.
"""

import abc
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from django.core.exceptions import SuspiciousFileOperation

_COPY_CHUNK = 1024 * 1024


class BlobTooLarge(Exception):
    """A local copy of a blob would exceed the size its caller allowed."""


class NameTaken(FileExistsError):
    """A save that must not overwrite found its name taken by another writer.

    Raised by a backend whose check and write are one request; BlobStorage
    then picks another name, as FileSystemStorage does on ``O_EXCL``.
    """


@dataclass(frozen=True)
class Entry:
    """One child of a directory, as :meth:`Backend.scan` lists it.

    Neither a directory nor a file is what a disk can hold besides: a symlink,
    a socket. Such an entry is listed so that its name still counts as taken.
    """

    name: str
    is_dir: bool
    is_file: bool


@dataclass(frozen=True)
class Blob:
    """A blob found by :meth:`Backend.iter_blobs`: its full name, its size and
    when it was last written (timezone-aware)."""

    name: str
    size: int
    modified: datetime


def checked_name(name):
    """*name*, refused when it could resolve to the root or above it.

    A move or a recursive delete on such a name reaches far past the node it
    was meant for, so the verbs check it themselves rather than trusting every
    caller to have built it right.
    """
    normalized = str(name).replace("\\", "/")
    if normalized.startswith("/") or any(
        part in ("", ".", "..") for part in normalized.split("/")
    ):
        raise SuspiciousFileOperation(f"Refusing to operate on storage name {name!r}")
    return name


@contextmanager
def temporary_copy(backend, name, *, max_bytes):
    """A local file holding the bytes of *name*, removed on exit.

    For a backend with no paths of its own: an external tool (ffprobe) needs a
    seekable file. Refused past *max_bytes* rather than filling the disk.
    """
    if backend.size(name) > max_bytes:
        raise BlobTooLarge(name)
    # Closed before the path is handed out: Windows refuses to let another
    # process open a temporary file that is still open for delete-on-close.
    with tempfile.NamedTemporaryFile(prefix="blob-", delete_on_close=False) as copy:
        with backend.open(name, "rb") as source:
            shutil.copyfileobj(source, copy, _COPY_CHUNK)
        copy.close()
        yield copy.name


class StagedWriter(abc.ABC):
    """Bytes written piecewise that only appear under their name on commit.

    Until then a reader still finds the previous version, and an abort leaves
    it in place: an interrupted upload never replaces a blob with a stump.
    """

    @abc.abstractmethod
    def write(self, data):
        """Append *data* (bytes-like) to the pending blob."""

    @abc.abstractmethod
    def commit(self):
        """Publish what was written under the name, replacing any previous blob."""

    @abc.abstractmethod
    def abort(self):
        """Drop what was written. Safe to call when nothing is left to drop."""


class Relocation(abc.ABC):
    """A move whose source is only dropped once the move is final."""

    @abc.abstractmethod
    def commit(self):
        """Drop what the move left at the source. Safe to call more than once."""


class Moved(Relocation):
    """A relocation the backend finished on the spot (a rename on a disk)."""

    def commit(self):
        pass


class Backend(abc.ABC):
    """Where the bytes live. Built by ``BlobStorage`` from its options."""

    # Django's Storage API, which BlobStorage passes straight through. The name
    # reaching save() is final: BlobStorage has already applied its policy.

    @abc.abstractmethod
    def open(self, name, mode="rb"):
        """A django File over the blob."""

    @abc.abstractmethod
    def save(self, name, content):
        """Write *content* under *name*; returns the name actually used."""

    @abc.abstractmethod
    def delete(self, name):
        """Remove the blob. A missing one is not an error."""

    @abc.abstractmethod
    def exists(self, name):
        """Whether anything, blob or directory, is stored under *name*."""

    @abc.abstractmethod
    def listdir(self, name):
        """``(directories, files)`` directly under *name*, as Django returns it."""

    @abc.abstractmethod
    def size(self, name):
        """The blob's size in bytes."""

    @abc.abstractmethod
    def url(self, name):
        """Where a client can fetch the blob."""

    def path(self, name):
        """The blob's filesystem path, on the one backend that has any."""
        raise NotImplementedError("This backend has no local paths.")

    def get_accessed_time(self, name):
        raise NotImplementedError("This backend does not record access times.")

    def get_created_time(self, name):
        raise NotImplementedError("This backend does not record creation times.")

    @abc.abstractmethod
    def get_modified_time(self, name):
        """An aware datetime of the blob's last write."""

    # What Django's API lacks.

    @abc.abstractmethod
    def is_dir(self, name):
        """Whether *name* is a directory holding anything, or an empty one kept."""

    @abc.abstractmethod
    def is_file(self, name):
        """Whether a blob is stored under *name*."""

    @abc.abstractmethod
    def scan(self, name):
        """The :class:`Entry` list of directory *name*'s children, in no order."""

    @abc.abstractmethod
    def iter_blobs(self, name):
        """Every :class:`Blob` under directory *name*, at any depth, in no order.

        Nothing under a missing directory: an empty iteration, not an error.
        """

    @abc.abstractmethod
    def iter_dirs(self, name):
        """The full name of every directory under *name*, at any depth."""

    @abc.abstractmethod
    def make_dir(self, name):
        """Keep directory *name* (and its parents) even while empty."""

    @abc.abstractmethod
    def remove_dir_if_empty(self, name):
        """Drop directory *name* if it holds nothing; returns whether it did."""

    @abc.abstractmethod
    def delete_prefix(self, name):
        """Remove directory *name* and everything in it; returns whether it existed."""

    @abc.abstractmethod
    def move(self, source, destination, *, overwrite=False):
        """Move a blob or a whole directory, creating the destination's parents.

        *overwrite* replaces a blob already at *destination*. Without it, a
        destination that exists is left to the backend to refuse or replace.
        """

    @abc.abstractmethod
    def relocate(self, source, destination):
        """Move a blob or a whole directory, keeping the source until commit.

        When this returns, the destination holds everything and a
        :class:`Relocation` is handed back. A backend that moves by copying
        keeps the source until its commit(): the caller points its rows at
        the destination and commits once they are durable, so a failure in
        between leaves duplicates behind, never a row pointing at nothing.
        Raises with the source untouched when the move could not complete.
        """

    @abc.abstractmethod
    def staged_writer(self, name):
        """A :class:`StagedWriter` that publishes under *name* on commit.

        Until then nothing of it is listed under any directory: a walk over
        the tree never mistakes an upload in flight for a blob.
        """

    @abc.abstractmethod
    def purge_staged(self, before):
        """Drop the staged writes left pending since before *before*.

        A writer that died mid-write never commits nor aborts, and its bytes
        would stay forever. A write is dated by its last byte where the
        backend records it, by its start otherwise. Returns how many went.
        """

    def signed_url(self, name, *, filename, attachment, content_type):
        """A short-lived URL a client can fetch the blob from directly, or None.

        None means the app serves the bytes itself. The URL carries the
        headers the app would have sent: *content_type*, and *filename* inline
        or as an *attachment*. Whoever holds it can fetch the blob until it
        expires, so it is only handed out after the access checks.
        """
        return None

    def local_path(self, name, *, max_bytes):
        """A context manager yielding a filesystem path holding the blob.

        Raises ``FileNotFoundError`` when the blob is missing, and
        :class:`BlobTooLarge` when a copy past *max_bytes* would be needed.
        """
        return temporary_copy(self, name, max_bytes=max_bytes)
