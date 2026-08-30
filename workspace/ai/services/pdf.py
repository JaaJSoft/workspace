"""Text extraction from PDF documents fetched by the web tools."""

import re
from dataclasses import dataclass

from workspace.common.documents.pdf import (
    iter_page_texts,
    open_pdf,
    page_count,
    read_metadata,
)

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


def extract_pdf(data: bytes, *, max_pages: int = MAX_PAGES) -> PdfDocument:
    """Extract the text of a PDF, reading at most *max_pages* pages.

    ``text`` comes back empty for an image-only PDF - a scan carries no text
    layer at all, and the caller is expected to say so rather than report an
    empty page.

    Raises ``ValueError`` when the document cannot be opened.
    """
    reader = open_pdf(data)
    pages = list(iter_page_texts(reader, max_pages=max_pages))
    joined = "\n\n".join(page.strip() for page in pages if page.strip())
    text = _BLANK_RUN_RE.sub("\n\n", _TRAILING_SPACE_RE.sub("\n", joined)).strip()
    title, date = read_metadata(reader)
    return PdfDocument(
        text=text,
        title=title,
        date=date,
        page_count=page_count(reader),
        pages_read=len(pages),
    )
