"""Full-text index over file names and, for text formats, file contents.

The content itself never reaches the database: extract_text() reads the blob,
index_document() binds the text as a statement parameter, and only the
resulting lexemes are stored. Nothing here can be rebuilt from the database
alone.

Writes happen off-request, from the files.index_search_document task, so a
rename or an edit shows up in search a moment later rather than instantly.
Every document records what it was built from (SearchIndexState), and the
hourly catch-up (services/catch_up.py) indexes again whatever no longer
matches: a file never indexed, a lost rename or edit, an extractor change.
"""

from __future__ import annotations

import logging
import re
import unicodedata

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from workspace.common.logging import scrub
from workspace.common.search.documents import drop_document, index_document
from workspace.common.search.schema import DerivedFulltextIndex, Field

from ..models import File, SearchIndexState
from .scanning.policy import exclude_blocked
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
    rowid_column="fts_rowid",
)

# Raise when extract_text() starts reading something it did not before (a new
# format, a larger cap): every document built by an older version becomes
# pending, and the catch-up re-extracts the library on its own.
EXTRACTOR_VERSION = 1


def build_document(file_obj):
    """The searchable document for *file_obj*: always a name, sometimes a body."""
    return {"name": file_obj.name or "", "body": extract_text(file_obj) or ""}


def index_file(file_obj):
    """(Re)index one file. Never raises - indexing is a side effect."""
    try:
        # Read outside the transaction, for the reason build_documents gives.
        document = build_document(file_obj)
        with transaction.atomic():
            index_document(FILES_FTS, file_obj.pk, document)
            if File.objects.filter(pk=file_obj.pk).exists():
                _record_state(file_obj)
    except Exception:
        logger.exception("Failed to index file %s", scrub(file_obj.pk))
        return False
    return True


def build_documents(file_objs):
    """Documents for a batch, as [(file, document)]. Reads blobs, writes nothing.

    Kept apart from the write so the extraction - a blob read per file, far
    slower than the statement it feeds - happens outside the transaction.
    Inside it, the write lock would be held for the whole batch and every
    other writer would stall behind it.
    """
    documents = []
    for file_obj in file_objs:
        try:
            documents.append((file_obj, build_document(file_obj)))
        except Exception:
            logger.exception("Failed to read file %s", scrub(file_obj.pk))
    return documents


def write_documents(documents):
    """Write a batch of built documents in one transaction. Returns the count.

    One transaction per batch rather than per file: the write lock is taken
    once for the batch, and an interrupted run keeps every batch it committed.
    """
    written = 0
    alive = set(
        File.objects.filter(pk__in=[f.pk for f, _ in documents]).values_list(
            "pk", flat=True
        )
    )
    with transaction.atomic():
        for file_obj, document in documents:
            try:
                # A savepoint per document, so one bad document rolls back
                # only itself.
                with transaction.atomic():
                    index_document(FILES_FTS, file_obj.pk, document)
                    if file_obj.pk in alive:
                        _record_state(file_obj)
                written += 1
            except Exception:
                logger.exception("Failed to index file %s", scrub(file_obj.pk))
    return written


def _record_state(file_obj):
    """Record what *file_obj*'s document was built from.

    Callers skip a row deleted meanwhile: index_document wrote nothing for it,
    and the foreign key would fail the commit of the whole batch.
    """
    # The values the document was built from, not a fresh read: a rename or
    # an edit landing meanwhile leaves the row behind the file, which is what
    # makes the catch-up index it again.
    SearchIndexState.objects.update_or_create(
        file_id=file_obj.pk,
        defaults={
            "name": file_obj.name or "",
            "content_hash": file_obj.content_hash,
            "extractor_version": EXTRACTOR_VERSION,
            "indexed_at": timezone.now(),
        },
    )


def pending_search_qs(*, reanalyze=False):
    """Live files whose search document is missing or stale.

    With *reanalyze*, every live file. Quarantined files are left out: their
    document is dropped on purpose, and restored when they are released.
    """
    qs = exclude_blocked(File.objects.filter(deleted_at__isnull=True))
    if reanalyze:
        return qs
    return qs.filter(
        Q(search_state__isnull=True)
        | ~Q(search_state__name=F("name"))
        | ~Q(search_state__content_hash=F("content_hash"))
        | Q(search_state__extractor_version__lt=EXTRACTOR_VERSION)
    )


def unindex_file(file_obj):
    """Remove a file from the index. Call before the row is deleted."""
    try:
        drop_document(FILES_FTS, file_obj.pk)
        SearchIndexState.objects.filter(file_id=file_obj.pk).delete()
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
