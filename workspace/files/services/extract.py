"""Extract ZIP archives into the user's file tree.

Security guardrails:
- File type must be in ZIP_LABELS (validated via file_obj.type, not MIME).
- Path entries are rejected on zip-slip (`..`), absolute paths, Windows drives.
- Symlink entries are silently skipped.
- Total uncompressed bytes and total entry count are capped (zip-bomb defence).
- The whole extraction runs in transaction.atomic() so partial failures roll back.
"""

import logging
import re
import zipfile

from django.conf import settings
from django.core.files.uploadedfile import TemporaryUploadedFile
from django.db import transaction

from workspace.common.logging import scrub
from ..models import File
from .files import FileService

logger = logging.getLogger(__name__)


class ArchiveTooLargeError(ValueError):
    """Raised when an archive's cumulative decompressed size exceeds the cap.

    Distinct from generic ``ValueError`` so the view layer can dispatch this
    case to HTTP 413 (Payload Too Large) instead of 400 (Bad Request) without
    relying on fragile substring matching of the message.
    """


class ArchiveTooManyEntriesError(ValueError):
    """Raised when an archive's entry count exceeds the cap.

    Distinct from generic ``ValueError`` so the view layer can dispatch this
    case to HTTP 413 (Payload Too Large) instead of 400 (Bad Request) without
    relying on fragile substring matching of the message.
    """


ZIP_LABELS = frozenset({'zip'})

_WINDOWS_DRIVE_RE = re.compile(r'^[A-Za-z]:')


def _is_unsafe_path(name):
    if not name:
        return True
    if name.startswith('/') or name.startswith('\\'):
        return True
    if _WINDOWS_DRIVE_RE.match(name):
        return True
    parts = name.replace('\\', '/').split('/')
    return any(p == '..' for p in parts)


def _is_symlink(info):
    """ZIP symlink bit lives in the high 16 bits of external_attr (unix mode)."""
    return (info.external_attr >> 16) & 0o170000 == 0o120000


_READ_CHUNK = 64 * 1024  # 64 KiB - same chunk size as the download-as-zip path.


def _stream_entry_to_tempfile(zf, info, leaf, total_bytes, max_bytes):
    """Decompress ``info`` into a ``TemporaryUploadedFile`` in chunks.

    Returns ``(tmp, new_total)``. The caller owns ``tmp`` and must close it.
    Raises ``ValueError`` ("Archive too large") when the running total exceeds
    ``max_bytes`` - this matches the project's download-side pattern (see
    ``ContentMixin._build_zip_stream``) of never holding more than one chunk
    in memory.

    ``TemporaryUploadedFile`` spills to disk past ``FILE_UPLOAD_MAX_MEMORY_SIZE``
    (default 2.5 MiB) and exposes ``_committed=False``, so ``FileField.pre_save``
    routes it through ``storage.save()`` which streams via ``.chunks()``.
    """
    tmp = TemporaryUploadedFile(
        name=leaf,
        content_type='application/octet-stream',
        size=0,
        charset=None,
    )
    entry_bytes = 0
    try:
        with zf.open(info, 'r') as src:
            while True:
                chunk = src.read(_READ_CHUNK)
                if not chunk:
                    break
                entry_bytes += len(chunk)
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise ArchiveTooLargeError("Archive too large")
                tmp.write(chunk)
        tmp.seek(0)
        # UploadedFile.size is a plain attribute set in __init__, NOT
        # recomputed from the underlying file after writes - leaving it at 0
        # made FileService.create_file persist size=0 for every extracted
        # entry. Set the real byte count, mirroring what Django's
        # TemporaryFileUploadHandler.file_complete does after an upload.
        tmp.size = entry_bytes
    except Exception:
        tmp.close()
        raise
    return tmp, total_bytes


def extract_zip(file_obj, dest_folder, *, acting_user):
    """Extract a .zip archive into ``dest_folder``.

    ``dest_folder`` may be ``None`` to extract into the user's root (no parent).

    Returns ``{'destination_uuid': str or None, 'files_created': int}``.
    Raises ``ValueError`` on bad MIME, zip-slip, oversize archives, or corruption.
    """
    if file_obj.node_type != File.NodeType.FILE:
        raise ValueError("Source is not a file")
    if file_obj.type not in ZIP_LABELS:
        raise ValueError("Not a ZIP archive")

    max_bytes = getattr(settings, 'FILES_EXTRACT_MAX_BYTES', 2 * 1024 * 1024 * 1024)
    max_entries = getattr(settings, 'FILES_EXTRACT_MAX_ENTRIES', 10_000)

    try:
        source = file_obj.content.open('rb')
    except (FileNotFoundError, OSError) as e:
        logger.warning("Cannot open archive %s: %s", scrub(file_obj.content.name), e)
        raise ValueError("Archive content missing") from e

    # Paths of blobs already written to storage. When the atomic block rolls
    # back, the File rows are gone but the storage blobs remain orphaned -
    # walk this list and delete them by hand.
    created_paths = []
    files_created = 0
    try:
        with source, zipfile.ZipFile(source) as zf:
            entries = zf.infolist()
            if len(entries) > max_entries:
                raise ArchiveTooManyEntriesError("Too many entries in archive")

            try:
                with transaction.atomic():
                    folder_cache = {(): dest_folder}
                    total_bytes = 0

                    for info in entries:
                        if _is_symlink(info):
                            continue
                        if _is_unsafe_path(info.filename):
                            raise ValueError(f"Unsafe entry in archive: {scrub(info.filename)}")

                        parts = [p for p in info.filename.replace('\\', '/').split('/') if p]
                        if not parts:
                            continue

                        if info.is_dir():
                            _ensure_folder_chain(parts, folder_cache, acting_user)
                            continue

                        parent = _ensure_folder_chain(parts[:-1], folder_cache, acting_user)
                        leaf = parts[-1]

                        tmp, total_bytes = _stream_entry_to_tempfile(
                            zf, info, leaf, total_bytes, max_bytes,
                        )
                        try:
                            new_file = FileService.create_file(
                                acting_user, leaf, parent=parent,
                                content=tmp,
                                acting_user=acting_user,
                            )
                            created_paths.append(
                                (new_file.content.storage, new_file.content.name),
                            )
                        finally:
                            tmp.close()
                        files_created += 1
            except Exception:
                # transaction.atomic already rolled back the DB rows. Clean up
                # the storage blobs that were written before the failure so we
                # don't leak orphans.
                for storage, path in created_paths:
                    try:
                        storage.delete(path)
                    except Exception:
                        logger.warning("Failed to clean orphan blob %s", scrub(path))
                raise
    except zipfile.BadZipFile as e:
        raise ValueError("Corrupted archive") from e

    return {
        'destination_uuid': str(dest_folder.uuid) if dest_folder is not None else None,
        'files_created': files_created,
    }


def _ensure_folder_chain(parts, folder_cache, acting_user):
    """Walk path segments, creating folders as needed. () maps to the destination."""
    current_key = ()
    current = folder_cache[current_key]
    for segment in parts:
        next_key = current_key + (segment,)
        if next_key in folder_cache:
            current = folder_cache[next_key]
            current_key = next_key
            continue

        existing = File.objects.filter(
            parent=current,
            name=segment,
            node_type=File.NodeType.FOLDER,
            deleted_at__isnull=True,
        ).first()
        if existing is None:
            existing = FileService.create_folder(
                acting_user, segment, parent=current, acting_user=acting_user,
            )
        folder_cache[next_key] = existing
        current = existing
        current_key = next_key
    return current
