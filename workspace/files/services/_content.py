"""Internal helpers for reading file content."""

from workspace.common.documents.extraction import (
    DOCUMENT_MIME_TYPES,
    MAX_DOCUMENT_BYTES,
    extract_document,
)

from ..models import File


def read_text_content(file_obj, *, max_bytes=32_768):
    """Read and return the text content of a file.

    A document is parsed rather than decoded, so that this agrees with search
    about which files hold words: one that could be found by a word inside it
    and then reported unreadable would be the pair coming apart. For those,
    *max_bytes* bounds the characters of prose returned rather than the bytes
    read, since the bytes are compressed and say nothing about the length.
    """
    if file_obj.node_type != File.NodeType.FILE:
        return None
    if not file_obj.content or not file_obj.content.name:
        return None
    if _is_document(file_obj):
        return _read_document(file_obj, max_chars=max_bytes)
    try:
        with file_obj.content.open("rb") as fh:
            raw = fh.read(max_bytes)
        return raw.decode("utf-8")
    except OSError, UnicodeDecodeError:
        return None


def _is_document(file_obj):
    mime = (file_obj.mime_type or "").split(";")[0].strip().lower()
    return mime in DOCUMENT_MIME_TYPES


def _read_document(file_obj, *, max_chars):
    """Prose of a PDF or office document, or None when there is none to show.

    A scan has no text layer and a damaged file has no text at all; both are
    "nothing to read" to a caller, which is what the decode path below also
    reports for a file it cannot make sense of.
    """
    try:
        with file_obj.content.open("rb") as fh:
            data = fh.read(MAX_DOCUMENT_BYTES)
        return extract_document(data, max_chars=max_chars).text or None
    except Exception:
        return None


def read_image_content(file_obj, *, max_bytes=10_485_760):
    """Read and return the raw bytes of an image file."""
    if file_obj.node_type != File.NodeType.FILE:
        return None, None
    if not file_obj.content or not file_obj.content.name:
        return None, None
    mime = file_obj.mime_type or ""
    if not mime.startswith("image/"):
        return None, None
    try:
        with file_obj.content.open("rb") as fh:
            raw = fh.read(max_bytes)
        return raw, mime
    except OSError:
        return None, None
