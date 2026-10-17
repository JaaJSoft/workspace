"""Read the properties a document declares about itself (DocumentInfo rows).

Title, author, subject, keywords, page count and the authoring dates of a PDF
or an office file, as its producer wrote them. Runs off-request as an upload
processor: in the upload pipeline once an upload or a content replacement has
committed, and from the hourly catch-up (services/processors.py) for whatever
that path missed.

The search index does not read these rows: it gets the same properties from
its own pass over the blob (see text_extraction.extract_text), so a document
is findable by its author whether or not this processor has run yet.
"""

import logging

from django.db.models import F, Q
from django.utils import timezone

from workspace.common.documents.extraction import MAX_DOCUMENT_BYTES, extract_document
from workspace.common.logging import scrub

from ..models import DocumentInfo, File
from .processors import register_processor
from .scanning.policy import exclude_blocked, is_blocked

logger = logging.getLogger(__name__)

# Magika's labels for the formats common.documents reads (DOCUMENT_MIME_TYPES).
DOCUMENT_LABELS = frozenset(
    {
        "pdf",
        "docx",
        "dotx",
        "xlsx",
        "pptx",
        "odt",
        "ods",
        "odp",
        "doc",
        "xls",
        "ppt",
        "rtf",
        "epub",
    }
)

_TEXT_FIELDS = ("title", "author", "subject", "keywords")
_MAX_LENGTHS = {
    name: DocumentInfo._meta.get_field(name).max_length for name in _TEXT_FIELDS
}


def is_document_candidate(file_obj):
    """True when *file_obj* is a document whose properties may be read.

    A quarantined file never is: reading its bytes is exactly what the malware
    policy forbids.
    """
    return (
        file_obj.node_type == File.NodeType.FILE
        and file_obj.type in DOCUMENT_LABELS
        and bool(file_obj.content)
        and not is_blocked(file_obj)
    )


def pending_qs(*, reanalyze=False):
    """Live documents whose DocumentInfo row is missing or stale.

    With *reanalyze*, every live document. A file whose own hash is empty only
    counts when it has no row at all, for the reason media_info.pending_qs
    gives.
    """
    qs = exclude_blocked(
        File.objects.alive().with_blob().filter(type__in=DOCUMENT_LABELS)
    )
    if reanalyze:
        return qs
    return qs.filter(
        Q(document_info__isnull=True)
        | (~Q(content_hash="") & ~Q(document_info__content_hash=F("content_hash")))
    )


def document_fields(document):
    """The DocumentInfo fields of an ExtractedDocument, or the empty ones."""
    if document is None:
        return {
            **dict.fromkeys(_TEXT_FIELDS, ""),
            "page_count": None,
            "created": None,
            "modified": None,
        }
    return {
        **{
            name: getattr(document, name)[: _MAX_LENGTHS[name]] for name in _TEXT_FIELDS
        },
        "page_count": document.page_count or None,
        "created": document.created,
        "modified": document.modified,
    }


def read_document_info(file_obj):
    """Read *file_obj*'s properties into its DocumentInfo row; return the row or None.

    None means nothing was written: the file is not a document, or the
    content was replaced while it was being read (the replacement's own event
    takes over). A blob that cannot be opened raises, so the failure ledger
    counts it. A document the parser cannot make sense of, or one too large to
    read, still gets a row, all empty: it has been looked at, and it would
    otherwise come back on every hourly pass.
    """
    if not is_document_candidate(file_obj):
        return None

    read_hash = file_obj.content_hash
    document = None
    if file_obj.size is not None and file_obj.size > MAX_DOCUMENT_BYTES:
        logger.info("Document %s is too large to read", scrub(file_obj.pk))
    else:
        with file_obj.content.open("rb") as fh:
            data = fh.read(MAX_DOCUMENT_BYTES)
        try:
            # Only the properties are kept: the text is the search index's.
            document = extract_document(data, max_chars=0)
        except ValueError as exc:
            logger.info(
                "Could not read the properties of %s: %s",
                scrub(file_obj.pk),
                scrub(exc),
            )

    current_hash = (
        File.objects.filter(pk=file_obj.pk)
        .values_list("content_hash", flat=True)
        .first()
    )
    if current_hash is None or current_hash != read_hash:
        return None

    info, _ = DocumentInfo.objects.update_or_create(
        file_id=file_obj.pk,
        defaults={
            **document_fields(document),
            "content_hash": read_hash,
            "extracted_at": timezone.now(),
        },
    )
    return info


def refresh_document_info(file_obj):
    """Read *file_obj*'s properties for the pipeline; True when its row was written."""
    return read_document_info(file_obj) is not None


def forget(file_obj):
    """Drop the row of a file that stopped being a document."""
    DocumentInfo.objects.filter(file_id=file_obj.pk).delete()


register_processor(
    "documents",
    applies_to=is_document_candidate,
    forget=forget,
    pending=pending_qs,
    process=refresh_document_info,
)
