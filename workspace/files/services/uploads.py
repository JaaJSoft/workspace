"""Uploads sent in chunks over several requests, resumable after a cut.

A session is opened with the file's name, folder and size, then chunks are
appended in order to a spool file in ``FILE_UPLOAD_TEMP_DIR``. That directory
sits on the volume the tree lives on, so finalizing places the file with a
rename, as for an upload sent in one request.

Chunks go in order because SHA-256 needs the bytes in order: each chunk is
hashed as it arrives, and finalizing reads nothing back. The hasher cannot be
stored with the session - hashlib objects do not serialize - so each worker
keeps the ones it holds in memory, and a worker that receives a chunk after
another one did catches up by reading that span back from the spool.
"""

import logging
import os
import uuid
from collections import OrderedDict
from contextlib import contextmanager

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files import locks
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.utils import timezone

from workspace.common.logging import scrub

from ..models import UploadSession
from .content_hash import new_hasher
from .quota import check_write_allowed

logger = logging.getLogger(__name__)

# What the client is told to send per request: small enough that a cut
# costs little and that a proxy capping request bodies (Cloudflare's free
# plan stops at 100 MB) lets it through, large enough that the round trips
# stay a small share of the transfer.
CHUNK_SIZE = 16 * 1024 * 1024

# What a single append accepts at most. Twice the advertised size, so a
# client is free to pick its own within reason.
MAX_CHUNK_SIZE = 2 * CHUNK_SIZE

_READ_SIZE = 1024 * 1024
_SPOOL_PREFIX = "session-"

# Hashers this worker holds, by session: (offset hashed up to, hasher).
_hashers = OrderedDict()
_HASHER_SLOTS = 32


class SessionGone(Exception):
    """The session was aborted, completed or expired, or its spool is missing."""


class SessionBusy(Exception):
    """Another request is appending to or finalizing the session."""


class OffsetMismatch(Exception):
    """A chunk that does not start where the received bytes end."""

    def __init__(self, offset):
        self.offset = offset
        super().__init__(f"The upload continues at byte {offset}.")


class ChunkIncomplete(Exception):
    """The request body ended before the chunk did; what came is kept."""

    def __init__(self, offset):
        self.offset = offset
        super().__init__(f"The chunk was cut at byte {offset}.")


class UploadIncomplete(Exception):
    """Finalizing a session that has not received all its bytes."""

    def __init__(self, offset):
        self.offset = offset
        super().__init__(f"Only {offset} bytes were received.")


class SpooledUpload(UploadedFile):
    """A finished spool, handed to ``FileService`` like a request upload.

    ``temporary_file_path`` is what lets the storage rename it into place
    instead of copying it, and ``content_hash`` what spares a second read.
    """

    def __init__(self, path, *, name, size, content_type, content_hash):
        super().__init__(
            open(path, "rb"), name=name, content_type=content_type, size=size
        )
        self._path = path
        self.content_hash = content_hash

    def temporary_file_path(self):
        return self._path


def spool_path(session):
    return os.path.join(
        settings.FILE_UPLOAD_TEMP_DIR, f"{_SPOOL_PREFIX}{session.pk.hex}.upload"
    )


def _lock_path(session):
    # The lock is not taken on the spool itself: Windows locks a byte range
    # mandatorily, and the storage reading the spool through a handle of its
    # own while finalizing would be refused.
    return os.path.join(
        settings.FILE_UPLOAD_TEMP_DIR, f"{_SPOOL_PREFIX}{session.pk.hex}.lock"
    )


@contextmanager
def _exclusive(session):
    """Hold the session's lock for the block, or raise ``SessionBusy``.

    An OS lock rather than a flag in the row: a worker killed mid-chunk
    releases it with its last file handle, where a flag would hold the
    session until somebody cleared it.
    """
    with open(_lock_path(session), "ab") as handle:
        if not locks.lock(handle, locks.LOCK_EX | locks.LOCK_NB):
            raise SessionBusy()
        try:
            yield
        finally:
            locks.unlock(handle)


def _lock_bucket(owner, group_id):
    """Serialise reservations in one bucket on databases with row locks.

    SQLite needs none: its transactions take the write lock when they begin.
    """
    if group_id is not None:
        Group.objects.select_for_update().filter(pk=group_id).first()
    else:
        get_user_model().objects.select_for_update().filter(pk=owner.pk).first()


def open_session(owner, *, name, parent, size, on_conflict, mime_type=""):
    """Reserve *size* bytes in the bucket and create an empty spool.

    Raises ``QuotaExceeded`` when the bucket cannot take them.
    """
    group_id = parent.group_id if parent is not None else None
    with transaction.atomic():
        _lock_bucket(owner, group_id)
        check_write_allowed(owner=owner, group=group_id, additional_bytes=size)
        session = UploadSession.objects.create(
            owner=owner,
            parent=parent,
            group_id=group_id,
            name=name,
            size=size,
            on_conflict=on_conflict,
            mime_type=mime_type or "",
        )
    try:
        os.makedirs(settings.FILE_UPLOAD_TEMP_DIR, exist_ok=True)
        with open(spool_path(session), "xb"):
            pass
    except OSError:
        session.delete()
        raise
    return session


def _received(session):
    """The session's received length as stored now, or ``SessionGone``."""
    received = (
        UploadSession.objects.filter(pk=session.pk, completed_at__isnull=True)
        .values_list("received", flat=True)
        .first()
    )
    if received is None:
        raise SessionGone()
    return received


def _resume_hasher(session, spool, offset):
    """A hasher fed with the spool's first *offset* bytes.

    Starts from the one this worker holds when there is one, reading back
    only what other workers received since.
    """
    held = _hashers.pop(session.pk, None)
    if held is not None and held[0] <= offset:
        position, hasher = held
    else:
        position, hasher = 0, new_hasher()
    spool.seek(position)
    while position < offset:
        piece = spool.read(min(_READ_SIZE, offset - position))
        if not piece:
            raise SessionGone()  # the spool lost bytes the row says it has
        hasher.update(piece)
        position += len(piece)
    return hasher


def _keep_hasher(session, offset, hasher):
    _hashers[session.pk] = (offset, hasher)
    while len(_hashers) > _HASHER_SLOTS:
        _hashers.popitem(last=False)


def append(session, start, stream, length):
    """Write *length* bytes read from *stream* at *start*; return the new offset.

    Raises ``OffsetMismatch`` when *start* is not where the received bytes
    end, ``SessionBusy`` while another request writes, and ``ChunkIncomplete``
    when *stream* ends early - the bytes that did arrive are kept, so the
    client resumes from there rather than from the start of the chunk.
    """
    if length <= 0 or start < 0 or start + length > session.size:
        raise ValueError("The chunk does not fit in the declared size.")
    try:
        spool = open(spool_path(session), "r+b")
    except FileNotFoundError:
        raise SessionGone() from None
    with spool, _exclusive(session):
        received = _received(session)
        if start != received:
            raise OffsetMismatch(received)
        hasher = _resume_hasher(session, spool, received)
        # Whatever lies past the received length is a chunk whose request
        # died before it could record it.
        spool.seek(start)
        spool.truncate()
        written = 0
        while written < length:
            try:
                piece = stream.read(min(_READ_SIZE, length - written))
            except OSError:
                break  # the client went away mid-chunk
            if not piece:
                break
            spool.write(piece)
            hasher.update(piece)
            written += len(piece)
        spool.flush()
        offset = start + written
        if written:
            moved = UploadSession.objects.filter(
                pk=session.pk, received=start, completed_at__isnull=True
            ).update(received=offset, updated_at=timezone.now())
            if not moved:
                raise SessionGone()
        _keep_hasher(session, offset, hasher)
    session.received = offset
    if written < length:
        raise ChunkIncomplete(offset)
    return offset


@contextmanager
def finished_upload(session):
    """The complete spool as a ``SpooledUpload``, under the session's lock.

    The lock holds while the caller stores it, so a retried finalize waits
    for the first one's answer instead of storing the file twice.
    """
    with _exclusive(session):
        received = _received(session)
        if received != session.size:
            raise UploadIncomplete(received)
        path = spool_path(session)
        try:
            content = SpooledUpload(
                path,
                name=session.name,
                size=session.size,
                content_type=session.mime_type or None,
                content_hash="",
            )
        except FileNotFoundError:
            raise SessionGone() from None
        with content:
            content.content_hash = _resume_hasher(
                session, content.file, received
            ).hexdigest()
            content.file.seek(0)
            yield content


def complete(session, file_obj, *, replaced):
    """Record what the session produced, which ends its reservation."""
    UploadSession.objects.filter(pk=session.pk).update(
        file=file_obj,
        replaced=replaced,
        completed_at=timezone.now(),
        updated_at=timezone.now(),
    )
    session.file = file_obj
    session.replaced = replaced
    _discard_files(session.pk)


def abort(session):
    """Delete the session with whatever it received."""
    UploadSession.objects.filter(pk=session.pk).delete()
    _discard_files(session.pk)


def _discard_files(pk):
    _hashers.pop(pk, None)
    session = UploadSession(pk=pk)
    for path in (spool_path(session), _lock_path(session)):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass  # moved into place, or never created
        except OSError as exc:
            logger.warning("Could not remove %s: %s", scrub(path), scrub(exc))


def purge_expired(before):
    """Delete the sessions untouched since *before*, and spools nobody owns.

    A spool outlives its row when the row goes with its owner or its folder,
    and a killed worker can leave one behind its aborted session. Returns how
    many sessions and orphan files were removed.
    """
    expired = list(
        UploadSession.objects.filter(updated_at__lt=before).values_list("pk", flat=True)
    )
    UploadSession.objects.filter(pk__in=expired).delete()
    for pk in expired:
        _discard_files(pk)
    removed = len(expired)

    try:
        entries = list(os.scandir(settings.FILE_UPLOAD_TEMP_DIR))
    except FileNotFoundError:
        return removed
    live = set(UploadSession.objects.values_list("pk", flat=True))
    cutoff = before.timestamp()
    for entry in entries:
        stem, _ext = os.path.splitext(entry.name)
        if not stem.startswith(_SPOOL_PREFIX):
            continue
        try:
            pk = uuid.UUID(stem.removeprefix(_SPOOL_PREFIX))
        except ValueError:
            continue
        if pk in live:
            continue
        try:
            if entry.stat(follow_symlinks=False).st_mtime >= cutoff:
                continue  # its session is being created right now
            os.remove(entry.path)
        except FileNotFoundError:
            continue
        removed += 1
    return removed
