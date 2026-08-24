"""Text extraction from PDF documents fetched by the web tools."""

import io
import logging
import re
from dataclasses import dataclass

from pypdf import PasswordType, PdfReader
from pypdf.errors import PdfReadError

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)

MAX_PAGES = 50

_BLANK_RUN_RE = re.compile(r"\n{3,}")
_TRAILING_SPACE_RE = re.compile(r"[ \t]+\n")


@dataclass(frozen=True)
class PdfDocument:
    """A PDF reduced to what a reader needs: its text and how much was left."""

    text: str
    title: str
    date: str
    page_count: int
    pages_read: int


def _open(data: bytes) -> PdfReader:
    """Open a PDF, mapping every way it can fail onto ``ValueError``."""
    try:
        reader = PdfReader(io.BytesIO(data))
    except (PdfReadError, OSError, RecursionError, ValueError) as exc:
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


def _metadata(reader: PdfReader) -> tuple[str, str]:
    """Return the document title and creation date, empty when unreadable."""
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


def _page_text(page, index: int) -> str:
    """Extract one page, swallowing the failures a single page can raise.

    A malformed font or content stream costs its own page; the rest of the
    document is still worth reading.
    """
    try:
        return page.extract_text() or ""
    except Exception as exc:
        logger.warning("PDF page %d unreadable: %s", index, scrub(exc))
        return ""


def extract_pdf(data: bytes, *, max_pages: int = MAX_PAGES) -> PdfDocument:
    """Extract the text of a PDF, reading at most *max_pages* pages.

    ``text`` comes back empty for an image-only PDF — a scan carries no text
    layer at all, and the caller is expected to say so rather than report an
    empty page.

    Raises ``ValueError`` when the document cannot be opened.
    """
    reader = _open(data)
    try:
        page_count = len(reader.pages)
        pages = [
            _page_text(reader.pages[i], i) for i in range(min(page_count, max_pages))
        ]
    except (PdfReadError, OSError, RecursionError) as exc:
        raise ValueError(f"Could not read PDF: {exc}") from exc

    joined = "\n\n".join(page.strip() for page in pages if page.strip())
    text = _BLANK_RUN_RE.sub("\n\n", _TRAILING_SPACE_RE.sub("\n", joined)).strip()
    title, date = _metadata(reader)
    return PdfDocument(
        text=text,
        title=title,
        date=date,
        page_count=page_count,
        pages_read=len(pages),
    )
