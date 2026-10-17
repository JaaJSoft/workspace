"""Text and metadata out of a document, whatever format it arrived in.

Apache Tika, compiled ahead of time to a native library. The set of formats
this app can look inside is therefore the set Tika knows, not the set someone
remembered to write a parser for - which is the whole reason for the
dependency. No JVM, no sidecar server, and no Python dependencies of its own:
one wheel per architecture.

The contract the callers rely on is narrow and unchanged. Every way a document
can be unreadable becomes a ValueError; a document with no text layer comes
back empty rather than raising, because a scan is a legitimate document that
happens to carry pictures of words; and the text is bounded by the extractor
rather than sliced afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from iscc_tika import Extractor

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DOTX = "application/vnd.openxmlformats-officedocument.wordprocessingml.template"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
ODT = "application/vnd.oasis.opendocument.text"
ODS = "application/vnd.oasis.opendocument.spreadsheet"
ODP = "application/vnd.oasis.opendocument.presentation"
DOC = "application/msword"
XLS = "application/vnd.ms-excel"
PPT = "application/vnd.ms-powerpoint"
RTF = "application/rtf"
EPUB = "application/epub+zip"

# What this app claims to look inside. Tika reads a great deal more, so this
# is a decision rather than a limit: a type earns its place here once someone
# has established that indexing its text is useful and that Tika reads it.
DOCUMENT_MIME_TYPES = frozenset(
    {PDF, DOCX, DOTX, XLSX, PPTX, ODT, ODS, ODP, DOC, XLS, PPT, RTF, EPUB}
)

# What a caller may page in to hand the parser. These formats are not
# prefix-readable - a zip's central directory and a PDF's cross-reference
# table both sit at the end - so the blob is read whole, and this is the only
# thing between a reader and a multi-gigabyte upload.
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024

# Tika pads its output with a little leading and trailing whitespace, so the
# probe that detects "there was more" has to clear the padding as well. Sixty
# four characters is far more than it ever emits and costs nothing to read.
_PROBE_CHARS = 64

_NPAGES = "xmpTPg:NPages"
_PAGE_COUNT = "meta:page-count"
_SLIDE_COUNT = "meta:slide-count"
_TITLE = "dc:title"
_CREATOR = "dc:creator"
_CREATED = "dcterms:created"
_MODIFIED = "dcterms:modified"
_ENCRYPTED = "pdf:encrypted"
# Tika folds a document's subject and its keywords into one dc:subject list,
# in no reliable order (the keywords come first for a PDF, last for a docx).
# The format-specific keys keep them apart.
_SUBJECTS = "dc:subject"
_SUBJECT_KEYS = ("cp:subject", "pdf:docinfo:subject")
_KEYWORD_KEYS = ("meta:keyword", "pdf:docinfo:keywords")


@dataclass(frozen=True)
class ExtractedDocument:
    """A document reduced to what a reader or an index needs."""

    text: str
    title: str
    date: str
    page_count: int
    encrypted: bool
    truncated: bool
    author: str
    subject: str
    keywords: str
    created: datetime | None
    modified: datetime | None


def extract_document(data: bytes, *, max_chars: int) -> ExtractedDocument:
    """Read *data* as a document, giving up at *max_chars* of text.

    Raises ValueError when the bytes are not a document any parser recognises.
    """
    try:
        # Past the ceiling on purpose: a document whose text is exactly
        # max_chars long is complete, and asking for exactly max_chars cannot
        # tell it apart from one that was cut there. What comes back beyond
        # the ceiling is the signal, and it is never returned.
        # extract_bytes_to_string will not take an immutable buffer.
        text, metadata = _extractor(max_chars + _PROBE_CHARS).extract_bytes_to_string(
            bytearray(data)
        )
    except Exception as exc:
        # Tika reports every parse failure as a TypeError carrying a Java
        # message. Catching the type it happens to use today would put us back
        # to guessing, so the boundary catches everything and promises one.
        raise ValueError(f"Could not read document: {exc}") from exc

    # Stripped before the comparison, not after: the padding is not content,
    # and counting it would report a document that ends exactly at the ceiling
    # as one that was cut short.
    text = text.strip()
    truncated = len(text) > max_chars
    text = text[:max_chars]
    keywords = _first_of(metadata, _KEYWORD_KEYS)
    return ExtractedDocument(
        text=text,
        title=_first(metadata, _TITLE),
        # Tika normalises every date it can parse to ISO 8601, so the calendar
        # day is the first ten characters of whatever the producer wrote.
        date=_first(metadata, _CREATED)[:10],
        page_count=_as_int(_first_of(metadata, (_NPAGES, _PAGE_COUNT, _SLIDE_COUNT))),
        encrypted=_first(metadata, _ENCRYPTED).lower() == "true",
        truncated=truncated,
        author=_first(metadata, _CREATOR),
        subject=_first_of(metadata, _SUBJECT_KEYS) or _subject(metadata, keywords),
        keywords=keywords,
        created=_as_datetime(_first(metadata, _CREATED)),
        modified=_as_datetime(_first(metadata, _MODIFIED)),
    )


def _extractor(max_chars: int) -> Extractor:
    # Imported here: every process loads this module at startup, and most of
    # them never read a document.
    from iscc_tika import Extractor, OfficeParserConfig, PdfOcrStrategy, PdfParserConfig

    office = (
        OfficeParserConfig()
        # Off by default, and both hold the words someone searches a document
        # by: the company name in a letterhead, the script under a slide.
        .set_include_headers_and_footers(True)
        .set_include_slide_notes(True)
    )
    pdf = (
        PdfParserConfig()
        # OCR is a different dependency with a different cost profile and is
        # deliberately out of scope; left on AUTO, Tika would look for a
        # Tesseract that the image does not ship.
        .set_ocr_strategy(PdfOcrStrategy.NO_OCR)
        .set_extract_inline_images(False)
    )
    return (
        Extractor()
        .set_extract_string_max_length(max_chars)
        .set_office_config(office)
        .set_pdf_config(pdf)
    )


def _first(metadata, key: str) -> str:
    """Tika reports every field as a list, even the ones that cannot repeat."""
    values = metadata.get(key) or []
    return str(values[0]).strip() if values else ""


def _first_of(metadata, keys) -> str:
    return next((value for key in keys if (value := _first(metadata, key))), "")


def _subject(metadata, keywords: str) -> str:
    """The dc:subject entry that is not the keywords, for the formats with
    no subject key of their own (docx, xlsx, pptx)."""
    for value in metadata.get(_SUBJECTS) or []:
        value = str(value).strip()
        if value and value != keywords:
            return value
    return ""


def _as_datetime(value: str) -> datetime | None:
    """An aware datetime, or None. OpenDocument dates carry no offset, and
    are read as UTC."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _as_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0
