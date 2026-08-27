"""Turn a file's stored blob into plain text for the search index.

Bounded and failure-tolerant by contract: an extractor either returns text or
None, never raises and never asks to be retried. A file whose body cannot be
extracted stays findable by name, which is what search did before this module
existed.

Extractors are keyed by MIME type. Formats that need a real dependency (PDF,
office documents) belong here as registrations once their converter exists,
not as special cases in the caller.
"""

from __future__ import annotations

import codecs
import html
import re

from django.utils.html import strip_tags

from ..models import File

# PostgreSQL rejects a tsvector built from more than ~1 MB of input; this sits
# an order of magnitude below that and bounds the SQLite side too.
BODY_CAP = 100_000

# UTF-8 is at most 4 bytes per character, so this is the widest read that can
# still be capped down to BODY_CAP characters.
_MAX_READ_BYTES = BODY_CAP * 4

_SCRIPT_OR_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.I | re.S)

_EXTRACTORS: dict[str, callable] = {}


def register_extractor(mime_type, extractor):
    """Register a plain-text extractor for one MIME type.

    The extractor takes the decoded text and returns plain text.
    """
    _EXTRACTORS[mime_type] = extractor


def extract_text(file_obj):
    """Plain text of *file_obj*'s content, or None when there is none to index."""
    if file_obj.node_type != File.NodeType.FILE:
        return None
    mime = _base_mime(file_obj.mime_type)
    extractor = _extractor_for(mime)
    if extractor is None:
        return None
    raw = _read_bounded(file_obj)
    if raw is None:
        return None
    text = _decode_utf8(raw)
    if text is None:
        return None
    text = extractor(text)[:BODY_CAP].strip()
    return text or None


def _base_mime(mime_type):
    return (mime_type or "").split(";")[0].strip().lower()


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
    return html.unescape(strip_tags(_SCRIPT_OR_STYLE_RE.sub(" ", text)))


register_extractor("text/html", _extract_html)


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
