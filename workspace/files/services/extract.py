"""Extract ZIP archives into the user's file tree.

Security guardrails:
- MIME must be application/zip (no auto-detect, no other formats).
- Path entries are rejected on zip-slip (`..`), absolute paths, Windows drives.
- Symlink entries are silently skipped.
- Total uncompressed bytes and total entry count are capped (zip-bomb defence).
- The whole extraction runs in transaction.atomic() so partial failures roll back.
"""

import logging
import re
import zipfile

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction

from workspace.common.logging import scrub
from ..models import File
from .files import FileService

logger = logging.getLogger(__name__)

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


_READ_CHUNK = 64 * 1024  # 64 KiB


def _read_capped(zf, info, total_bytes, max_bytes):
    """Decompress entry contents in chunks, raising ValueError if max_bytes is exceeded.

    Enforces the cap against the actually decompressed bytes, not the (untrusted)
    ``ZipInfo.file_size`` header field, which a malicious archive can under-report
    to slip past the cap and then expand to a much larger blob.

    Returns (bytes, new_total).
    """
    chunks = []
    with zf.open(info, 'r') as fh:
        while True:
            chunk = fh.read(_READ_CHUNK)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > max_bytes:
                raise ValueError("Archive too large")
            chunks.append(chunk)
    return b''.join(chunks), total_bytes


def extract_zip(file_obj, dest_folder, *, acting_user):
    """Extract a .zip archive into ``dest_folder``.

    ``dest_folder`` may be ``None`` to extract into the user's root (no parent).

    Returns ``{'destination_uuid': str or None, 'files_created': int}``.
    Raises ``ValueError`` on bad MIME, zip-slip, oversize archives, or corruption.
    """
    if file_obj.node_type != File.NodeType.FILE:
        raise ValueError("Source is not a file")
    if file_obj.mime_type != 'application/zip':
        raise ValueError("Not a ZIP archive")

    max_bytes = getattr(settings, 'FILES_EXTRACT_MAX_BYTES', 2 * 1024 * 1024 * 1024)
    max_entries = getattr(settings, 'FILES_EXTRACT_MAX_ENTRIES', 10_000)

    try:
        source = file_obj.content.open('rb')
    except (FileNotFoundError, OSError) as e:
        logger.warning("Cannot open archive %s: %s", scrub(file_obj.content.name), e)
        raise ValueError("Archive content missing") from e

    files_created = 0
    try:
        with source, zipfile.ZipFile(source) as zf:
            entries = zf.infolist()
            if len(entries) > max_entries:
                raise ValueError("Too many entries in archive")

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

                    data, total_bytes = _read_capped(zf, info, total_bytes, max_bytes)
                    FileService.create_file(
                        acting_user, leaf, parent=parent,
                        content=ContentFile(data, name=leaf),
                        acting_user=acting_user,
                    )
                    files_created += 1
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
