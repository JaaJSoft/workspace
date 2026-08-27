"""Full-text index over file names and, for text formats, file contents.

The content itself never reaches the database: extract_text() reads the blob,
index_document() binds the text as a statement parameter, and only the
resulting lexemes are stored. Nothing here can be rebuilt from the database
alone, which is why reindex_files_search exists.

Writes happen off-request, from the files.index_search_document task, so a
rename or an edit shows up in search a moment later rather than instantly.
"""

from __future__ import annotations

import logging
import re
import unicodedata

from workspace.common.logging import scrub
from workspace.common.search.documents import drop_document, index_document
from workspace.common.search.schema import DerivedFulltextIndex, Field

from .text_extraction import BODY_CAP, extract_text

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"\w+", re.UNICODE)

# Field order is frozen into the applied bm25 config bm25(10.0, 2.0): name A,
# body C. Do not reorder without a migration. `body` is not a column, so only
# `name` can serve the icontains fallback - without FTS5 there is no content
# search at all.
FILES_FTS = DerivedFulltextIndex(
    table="files_file",
    fields=(Field("name", weight="A"), Field("body", weight="C", cap=BODY_CAP)),
    fallback_fields=("name",),
)


def build_document(file_obj):
    """The searchable document for *file_obj*: always a name, sometimes a body."""
    return {"name": file_obj.name or "", "body": extract_text(file_obj) or ""}


def index_file(file_obj):
    """(Re)index one file. Never raises - indexing is a side effect."""
    try:
        index_document(FILES_FTS, file_obj.pk, build_document(file_obj))
    except Exception:
        logger.exception("Failed to index file %s", scrub(file_obj.pk))
        return False
    return True


def unindex_file(file_obj):
    """Remove a file from the index. Call before the row is deleted."""
    try:
        drop_document(FILES_FTS, file_obj.pk)
    except Exception:
        logger.exception("Failed to unindex file %s", scrub(file_obj.pk))
        return False
    return True


def match_type_for(name, query):
    """ "name" when the query is visible in the file name, "content" otherwise.

    The index cannot say which field matched, and the body is not stored, so
    there is no snippet to show either: the UI only needs to know whether the
    hit is explained by the name the user is looking at.
    """
    haystack = _normalize(name)
    tokens = [_normalize(t) for t in _WORD_RE.findall(query or "")]
    if tokens and all(token in haystack for token in tokens):
        return "name"
    return "content"


def _normalize(text):
    stripped = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in stripped if not unicodedata.combining(c)).casefold()
