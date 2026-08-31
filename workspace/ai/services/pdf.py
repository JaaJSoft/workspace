"""Text extraction from PDF documents fetched by the web tools."""

import logging
import re

from workspace.common.documents.extraction import ExtractedDocument, extract_document
from workspace.common.logging import scrub

logger = logging.getLogger(__name__)

# What a reader can usefully be handed from one document. The web tool trims
# again to its own budget; this is the ceiling on what is read at all.
MAX_CHARS = 200_000

_BLANK_RUN_RE = re.compile(r"\n{3,}")
_TRAILING_SPACE_RE = re.compile(r"[ \t]+\n")


def extract_pdf(data: bytes, *, max_chars: int = MAX_CHARS) -> ExtractedDocument:
    """Extract the text of a PDF, reading at most *max_chars* characters.

    ``text`` comes back empty for an image-only PDF - a scan carries no text
    layer at all, and the caller is expected to say so rather than report an
    empty page.

    Raises ``ValueError`` when the document cannot be opened, or when it is
    encrypted with a password we do not have.
    """
    try:
        document = extract_document(data, max_chars=max_chars)
    except ValueError as exc:
        # The underlying message names Tika classes and a Java object address.
        # A reader is told what happened; which parser said so goes to the log.
        logger.info("PDF could not be read: %s", scrub(exc))
        raise ValueError("Could not read PDF") from exc
    if document.encrypted and not document.text:
        # A PDF encrypted with an empty user password only restricts what a
        # reader may do with it, and Tika reads those; one that comes back
        # with nothing to show is genuinely locked.
        raise ValueError("PDF is password-protected")
    text = _BLANK_RUN_RE.sub("\n\n", _TRAILING_SPACE_RE.sub("\n", document.text))
    return ExtractedDocument(
        text=text.strip(),
        title=document.title,
        date=document.date,
        page_count=document.page_count,
        encrypted=document.encrypted,
        truncated=document.truncated,
    )
