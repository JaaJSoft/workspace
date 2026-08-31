"""Turn a file's stored blob into plain text.

The one way this application does that. Search reads a file's body through
here, and so does the reader behind the assistant's read_file tool, because
two ways of answering "what words are in this file" is two answers that drift
apart: search finding a word the reader then cannot show is the shape that bug
takes.

Everything goes to the same parser, whatever the format. What is left in this
module is policy rather than parsing - which types are worth looking inside at
all, since a photograph and a video hold no words and reading them would cost
a blob apiece for nothing - and the ceilings that bound the read.

Failure-tolerant by contract: this returns text or None, never raises and
never asks to be retried. A file whose body cannot be read stays findable by
its name.
"""

from __future__ import annotations

import logging

from workspace.common.documents.extraction import (
    DOCUMENT_MIME_TYPES,
    MAX_DOCUMENT_BYTES,
    extract_document,
)
from workspace.common.logging import scrub

from ..models import File
from .detection import detect_from_name

logger = logging.getLogger(__name__)

# PostgreSQL rejects a tsvector built from more than ~1 MB of input; this sits
# an order of magnitude below that and bounds the SQLite side too.
BODY_CAP = 100_000

# Types the app files under "text" with a text viewer, so a user reading one on
# screen would not understand why search cannot see the words in front of them.
# The parser hands these back as they are written, which is what someone
# searching an XML or JSON file is after: the element and key names.
_TEXT_LIKE_APPLICATION_TYPES = frozenset(
    {
        "application/json",
        "application/xml",
        "application/javascript",
        "application/x-python-code",
    }
)

INDEXABLE_MIME_TYPES = DOCUMENT_MIME_TYPES | _TEXT_LIKE_APPLICATION_TYPES


def is_indexable(mime_type):
    """Whether this application looks inside a file of this type."""
    mime = _base_mime(mime_type)
    # Any other text/* subtype is worth reading as-is: better a slightly noisy
    # document than a file nobody can find by its contents.
    return mime in INDEXABLE_MIME_TYPES or mime.startswith("text/")


def file_text(file_obj, *, max_chars):
    """Plain text of *file_obj*'s content, or None when there is none.

    The catch is deliberately broad: the parser is pointed at whatever a user
    chose to upload, and every way one of these files can be unreadable -
    encrypted, truncated, a scan with no text layer - has to land on the same
    answer rather than on an exception the caller never planned for.
    """
    if file_obj.node_type != File.NodeType.FILE:
        return None
    if not _looks_indexable(file_obj):
        return None
    if not file_obj.content or not file_obj.content.name or _too_large(file_obj):
        return None
    try:
        with file_obj.content.open("rb") as fh:
            data = fh.read(MAX_DOCUMENT_BYTES)
        return extract_document(data, max_chars=max_chars).text or None
    except Exception as exc:
        logger.info(
            "No text extracted from file %s: %s", scrub(file_obj.pk), scrub(exc)
        )
        return None


def extract_text(file_obj):
    """The searchable body of a file, bounded to what the index accepts."""
    return file_text(file_obj, max_chars=BODY_CAP)


def _looks_indexable(file_obj):
    """Whether *file_obj* is worth reading, by its type or failing that its name.

    mime_type is nullable, and detection falls back to application/octet-stream
    for content it cannot place, so the column alone would leave a file called
    report.docx unread on the strength of a guess that failed.
    """
    if is_indexable(file_obj.mime_type):
        return True
    return is_indexable(detect_from_name(file_obj.name).mime_type)


def _base_mime(mime_type):
    return (mime_type or "").split(";")[0].strip().lower()


def _too_large(file_obj):
    size = file_obj.size
    if size is None:
        try:
            size = file_obj.content.size
        except OSError, ValueError:
            # Vanished blob or a storage that cannot stat it: the read that
            # follows would fail too.
            return True
    return size > MAX_DOCUMENT_BYTES
