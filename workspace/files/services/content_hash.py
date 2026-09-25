"""Content hashing for file rows and the duplicate lookup built on it."""

import hashlib
import logging

from workspace.common.logging import scrub

from ..models import File
from .catch_up import register_catch_up

logger = logging.getLogger(__name__)

HASH_ALGORITHM = "sha256"
_CHUNK_SIZE = 64 * 1024


def new_hasher():
    """Incremental hasher for callers that already stream the bytes once."""
    return hashlib.new(HASH_ALGORITHM)


def hash_stream(stream) -> str:
    """Hex digest of the whole of *stream*, left at the position it started at."""
    seekable = hasattr(stream, "seek") and hasattr(stream, "tell")
    pos = stream.tell() if seekable else None
    hasher = new_hasher()
    if hasattr(stream, "chunks"):
        # django.core.files.File.chunks() rewinds to the start itself.
        for chunk in stream.chunks(_CHUNK_SIZE):
            hasher.update(chunk)
    else:
        if seekable:
            stream.seek(0)
        while chunk := stream.read(_CHUNK_SIZE):
            hasher.update(chunk)
    if seekable:
        stream.seek(pos)
    return hasher.hexdigest()


def hash_storage_file(storage, name) -> str:
    """Hex digest of the blob stored at *name*."""
    with storage.open(name, "rb") as f:
        return hash_stream(f)


def find_duplicates(file_obj):
    """Live files with the same content as *file_obj*, within its scope.

    Matched on hash and size, so a hash collision or a stale hash never
    turns two different files into "duplicates".

    The scope is the owner's personal files for a personal file and the
    group's files for a group file - never anyone else's, so a match cannot
    reveal what another user has uploaded.
    """
    if not file_obj.content_hash:
        return File.objects.none()
    qs = File.objects.filter(
        content_hash=file_obj.content_hash,
        size=file_obj.size,
        node_type=File.NodeType.FILE,
        deleted_at__isnull=True,
    ).exclude(pk=file_obj.pk)
    if file_obj.group_id:
        qs = qs.filter(group_id=file_obj.group_id)
    else:
        qs = qs.filter(owner_id=file_obj.owner_id, group__isnull=True)
    return qs.name_ordered()


def pending_hash_qs(*, reanalyze=False):
    """File rows holding a blob but no hash: registered before hashes existed,
    or by the disk sync while their blob was unreadable.

    With *reanalyze*, every file row holding a blob.
    """
    qs = File.objects.with_blob()
    return qs if reanalyze else qs.filter(content_hash="")


def refresh_content_hash(file_obj):
    """Hash *file_obj*'s blob into its row; True when the hash was written.

    False when the blob cannot be read, or when the row's hash changed while
    the blob was being read: the content write that changed it stored the
    digest of the new bytes, which must not be overwritten.
    """
    try:
        digest = hash_storage_file(file_obj.content.storage, file_obj.content.name)
    except OSError as exc:
        logger.warning(
            "Cannot hash the blob of %s: %s",
            scrub(file_obj.content.name),
            scrub(str(exc)),
        )
        return False
    written = File.objects.filter(
        pk=file_obj.pk, content_hash=file_obj.content_hash
    ).update(content_hash=digest)
    return bool(written)


register_catch_up("content_hash", pending=pending_hash_qs, process=refresh_content_hash)
