"""Turn a file's stored blob into plain text for the search index.

Bounded and failure-tolerant by contract: an extractor either returns text or
None, never raises and never asks to be retried. A file whose body cannot be
extracted stays findable by name, which is what search did before this module
existed.

Extractors are keyed by MIME type and come in two shapes, because the formats
do. A text format is decoded from a bounded prefix of the blob and handed to
its extractor as a string. A PDF or an office document cannot be read from a
prefix at all - a zip's central directory and a PDF's cross-reference table
both sit at the end of the file - so those are read whole and handed to Tika,
which bounds its own output.
"""

from __future__ import annotations

import codecs
import html
import logging
import re

from django.utils.html import strip_tags

from workspace.common.documents.extraction import (
    DOCUMENT_MIME_TYPES,
    extract_document,
)
from workspace.common.logging import scrub

from ..models import File

logger = logging.getLogger(__name__)

# PostgreSQL rejects a tsvector built from more than ~1 MB of input; this sits
# an order of magnitude below that and bounds the SQLite side too.
BODY_CAP = 100_000

# UTF-8 is at most 4 bytes per character, so this is the widest read that can
# still be capped down to BODY_CAP characters.
_MAX_READ_BYTES = BODY_CAP * 4

# What a stream extractor may page in. Its format is not prefix-readable, so
# the blob is opened whole and this is the only thing standing between the
# indexer and a multi-gigabyte upload. Generous next to any document that
# carries BODY_CAP characters of prose, which is roughly sixty pages.
_MAX_DOCUMENT_BYTES = 64 * 1024 * 1024

_SCRIPT_OR_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.I | re.S)

_WHITESPACE_RE = re.compile(r"\s+")

_EXTRACTORS: dict[str, callable] = {}

_BYTES_EXTRACTORS: dict[str, callable] = {}


def register_extractor(mime_type, extractor):
    """Register a plain-text extractor for one MIME type.

    The extractor takes the decoded text and returns plain text.
    """
    _EXTRACTORS[mime_type] = extractor


def register_bytes_extractor(mime_type, extractor):
    """Register an extractor for a format that has to be read whole.

    The extractor takes the blob's bytes and returns plain text already
    bounded to BODY_CAP: a format nobody can read from a prefix is also one
    that has to stop at the ceiling rather than extract everything and slice.
    """
    _BYTES_EXTRACTORS[mime_type] = extractor


def extract_text(file_obj):
    """Plain text of *file_obj*'s content, or None when there is none to index."""
    if file_obj.node_type != File.NodeType.FILE:
        return None
    mime = _base_mime(file_obj.mime_type)
    bytes_extractor = _BYTES_EXTRACTORS.get(mime)
    if bytes_extractor is not None:
        return _extract_from_bytes(file_obj, bytes_extractor) or None
    return _extract_from_prefix(file_obj, mime) or None


def has_extractor(mime_type):
    """Whether *mime_type* has an extractor at all.

    Exists so the app's own catalogue of known types can be checked against
    this registry: a format nobody registered is a format nobody notices.
    """
    mime = _base_mime(mime_type)
    return mime in _BYTES_EXTRACTORS or _extractor_for(mime) is not None


def _base_mime(mime_type):
    return (mime_type or "").split(";")[0].strip().lower()


def _extract_from_prefix(file_obj, mime):
    extractor = _extractor_for(mime)
    if extractor is None:
        return None
    raw = _read_bounded(file_obj)
    if raw is None:
        return None
    text = _decode_utf8(raw)
    if text is None:
        return None
    return extractor(text)[:BODY_CAP].strip()


def _extract_from_bytes(file_obj, extractor):
    """Run a whole-blob extractor, or give up quietly.

    Every way one of these documents can be unreadable - encrypted, truncated,
    a scan with no text layer - lands here and leaves the file findable by its
    name. The catch is deliberately broad: the parser is pointed at whatever a
    user chose to upload, and the module's contract is that no input turns
    indexing into a failure.
    """
    if not file_obj.content or not file_obj.content.name or _too_large(file_obj):
        return None
    try:
        with file_obj.content.open("rb") as fh:
            return extractor(fh.read(_MAX_DOCUMENT_BYTES))[:BODY_CAP].strip()
    except Exception as exc:
        logger.info(
            "No text extracted from file %s: %s", scrub(file_obj.pk), scrub(exc)
        )
        return None


def _too_large(file_obj):
    size = file_obj.size
    if size is None:
        try:
            size = file_obj.content.size
        except OSError, ValueError:
            # Vanished blob or a storage that cannot stat it: the read that
            # follows would fail too.
            return True
    return size > _MAX_DOCUMENT_BYTES


def _extractor_for(mime):
    if mime in _EXTRACTORS:
        return _EXTRACTORS[mime]
    # Any other text/* subtype is worth indexing as-is: better a slightly
    # noisy document than a file nobody can find by its contents.
    return _extract_plain if mime.startswith("text/") else None


def _extract_plain(text):
    return text


def _extract_html(text):
    # Script and style bodies are markup payload, not prose: strip_tags alone
    # would keep their contents and index minified JavaScript.
    text = _SCRIPT_OR_STYLE_RE.sub(" ", text)
    # strip_tags leaves nothing where a tag was, so "<h1>Title</h1><p>Body</p>"
    # collapses to the single token "TitleBody" and neither word is findable.
    # A space in front of every "<" restores the boundary - it can only add a
    # separator, never swallow content, and unlike a tag-shaped regex it leaves
    # the actual parsing to strip_tags.
    text = html.unescape(strip_tags(text.replace("<", " <")))
    # Markup indentation would otherwise eat into the character budget that
    # real prose needs.
    return _WHITESPACE_RE.sub(" ", text).strip()


register_extractor("text/html", _extract_html)

# Text the app already treats as text: MimeTypeRule files these under the
# "text" category with the text viewer, so a user reading one on screen would
# not understand why search cannot see the words in front of them. Indexed
# raw - element and key names are usually what someone searches an XML or
# JSON file for.
for _mime in (
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-python-code",
):
    register_extractor(_mime, _extract_plain)


def _extract_document(data):
    return extract_document(data, max_chars=BODY_CAP).text


# One extractor for every document format, because one library reads them all.
# The list lives with that library, so adding a format is a decision recorded
# in one place rather than a parser written here.
for _mime in sorted(DOCUMENT_MIME_TYPES):
    register_bytes_extractor(_mime, _extract_document)


def _read_bounded(file_obj):
    if not file_obj.content or not file_obj.content.name:
        return None
    try:
        with file_obj.content.open("rb") as fh:
            return fh.read(_MAX_READ_BYTES)
    except OSError, ValueError:
        # Vanished blob, unreadable mount, or a FieldFile whose storage is
        # gone. Nothing to index and nothing worth retrying.
        return None


def _decode_utf8(raw):
    """Decode as UTF-8, tolerating a read that ends mid-character.

    The incremental decoder holds back an incomplete trailing sequence instead
    of failing, so capping the read never costs more than the last character.
    Genuinely invalid bytes still raise, which is how binary content declared
    as text/* is rejected.
    """
    try:
        return codecs.getincrementaldecoder("utf-8")().decode(raw, final=False)
    except UnicodeDecodeError:
        return None
