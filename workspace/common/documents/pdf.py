"""Reading text out of a PDF.

Two callers want different halves of this. A reader fetching a document from
the web wants the whole of a small file plus its metadata; an indexer wants as
many pages as fit a character budget and nothing else. What they share is the
error contract: every way a document can fail to open becomes a ValueError,
and a page that cannot be rendered costs its own page and no more.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator

from pypdf import PasswordType, PdfReader
from pypdf.errors import PdfReadError

from workspace.common.logging import scrub

from .budget import TextBudget

logger = logging.getLogger(__name__)

# What pypdf raises for a document it cannot navigate at all, as opposed to the
# per-page failures a single unsupported font causes.
_STRUCTURAL_ERRORS = (PdfReadError, OSError, RecursionError, ValueError)


def open_pdf(source) -> PdfReader:
    """Open a PDF from bytes or a seekable stream.

    Raises ValueError when the document cannot be read, or when it is
    encrypted with a password we do not have.
    """
    stream = io.BytesIO(source) if isinstance(source, bytes | bytearray) else source
    try:
        reader = PdfReader(stream)
    except _STRUCTURAL_ERRORS as exc:
        raise ValueError(f"Could not read PDF: {exc}") from exc

    if not reader.is_encrypted:
        return reader
    # An empty user password is the "printing restricted" case, which every
    # reader opens; a real one leaves the pages encrypted.
    try:
        unlocked = reader.decrypt("") != PasswordType.NOT_DECRYPTED
    except Exception as exc:
        logger.debug("PDF decryption failed: %s", scrub(exc))
        unlocked = False
    if not unlocked:
        raise ValueError("PDF is password-protected")
    return reader


def page_count(reader: PdfReader) -> int:
    """How many pages *reader* holds. Raises ValueError on a broken page tree."""
    try:
        return len(reader.pages)
    except _STRUCTURAL_ERRORS as exc:
        raise ValueError(f"Could not read PDF: {exc}") from exc


def iter_page_texts(reader: PdfReader, *, max_pages: int) -> Iterator[str]:
    """Yield the text of each of the first *max_pages* pages, in order."""
    for index in range(min(page_count(reader), max_pages)):
        try:
            page = reader.pages[index]
        except _STRUCTURAL_ERRORS as exc:
            raise ValueError(f"Could not read PDF: {exc}") from exc
        yield page_text(page, index)


def page_text(page, index: int) -> str:
    """Extract one page, swallowing the failures a single page can raise.

    A malformed font or content stream costs its own page; the rest of the
    document is still worth reading.
    """
    try:
        return page.extract_text() or ""
    except Exception as exc:
        logger.warning("PDF page %d unreadable: %s", index, scrub(exc))
        return ""


def read_metadata(reader: PdfReader) -> tuple[str, str]:
    """The document title and creation date, empty when unreadable."""
    try:
        meta = reader.metadata
        if meta is None:
            return "", ""
        title = (meta.title or "").strip()
        created = meta.creation_date
    except Exception as exc:
        logger.debug("PDF metadata unreadable: %s", scrub(exc))
        return "", ""
    return title, created.date().isoformat() if created else ""


def pdf_text(source, *, max_chars: int, max_pages: int) -> str:
    """Text of a PDF, stopping at the page that fills *max_chars*.

    Empty for a scan: an image-only page carries no text layer at all. Raises
    ValueError when the document cannot be opened.
    """
    budget = TextBudget(max_chars)
    for text in iter_page_texts(open_pdf(source), max_pages=max_pages):
        stripped = text.strip()
        if stripped:
            budget.add(stripped, separator="\n\n")
        if budget.full:
            break
    return budget.text()
