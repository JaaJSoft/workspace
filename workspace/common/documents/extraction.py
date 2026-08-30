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

from iscc_tika import Extractor, OfficeParserConfig, PdfOcrStrategy, PdfParserConfig

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

_NPAGES = "xmpTPg:NPages"
_TITLE = "dc:title"
_CREATED = "dcterms:created"
_ENCRYPTED = "pdf:encrypted"


@dataclass(frozen=True)
class ExtractedDocument:
    """A document reduced to what a reader or an index needs."""

    text: str
    title: str
    date: str
    page_count: int
    encrypted: bool
    truncated: bool


def extract_document(data: bytes, *, max_chars: int) -> ExtractedDocument:
    """Read *data* as a document, giving up at *max_chars* of text.

    Raises ValueError when the bytes are not a document any parser recognises.
    """
    try:
        # extract_bytes_to_string will not take an immutable buffer.
        text, metadata = _extractor(max_chars).extract_bytes_to_string(bytearray(data))
    except Exception as exc:
        # Tika reports every parse failure as a TypeError carrying a Java
        # message. Catching the type it happens to use today would put us back
        # to guessing, so the boundary catches everything and promises one.
        raise ValueError(f"Could not read document: {exc}") from exc

    # Measured before stripping: the extractor fills its budget with whatever
    # the document holds, trailing whitespace included, and trimming that away
    # afterwards would hide the fact that it stopped early.
    truncated = len(text) >= max_chars
    text = text.strip()
    return ExtractedDocument(
        text=text,
        title=_first(metadata, _TITLE),
        # Tika normalises every date it can parse to ISO 8601, so the calendar
        # day is the first ten characters of whatever the producer wrote.
        date=_first(metadata, _CREATED)[:10],
        page_count=_as_int(_first(metadata, _NPAGES)),
        encrypted=_first(metadata, _ENCRYPTED).lower() == "true",
        truncated=truncated,
    )


def _extractor(max_chars: int) -> Extractor:
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


def _as_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0
